"""Generate and render ONE narrated video for one concept of one stored paper.

WARNING: THIS SPENDS MONEY WHEN RUN. It makes real LLM calls (planner, generator,
narration judge, retries) and real TTS calls while rendering. Never run it from
tests or CI; pytest does not collect tools/ (testpaths=["tests"]).

The paper and its sections are read from the local DB (SQLite ./arxiviz.db unless
DATABASE_URL is set) and nothing is written to it. The pipeline runs with the
NARRATED profile and the same gates generate_visualizations wires up; the code is
then rendered at low quality into --out. Requires VISUAL_NARRATION=1 (or pass
--visual-narration) so the planner/generator draw quantities instead of writing them.

Usage (from backend/):
    uv run python tools/run_single_concept.py --paper 1512.03385 \
        --concept "Bottleneck Residual Block" --visual-narration
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Claude Sonnet 5.5 list prices, USD per million tokens (an ESTIMATE; check billing).
PRICE_PER_MTOK = {"in": 2.00, "out": 10.00, "cache_read": 0.20, "cache_write": 2.50}

_USAGE_RE = re.compile(
    r"\[LLM\].*?in=(\w+) out=(\w+) cache_read=(\w+) cache_write=(\w+)"
)


class UsageCollector(logging.Handler):
    """Collects the "[LLM] ... in=.. out=.. cache_read=.. cache_write=.." lines
    that agents/base.py logs for every Anthropic call."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.calls: list[dict[str, int]] = []

    def emit(self, record: logging.LogRecord) -> None:
        m = _USAGE_RE.search(record.getMessage())
        if not m:
            return
        keys = ("in", "out", "cache_read", "cache_write")
        self.calls.append({k: int(v) if v.isdigit() else 0 for k, v in zip(keys, m.groups(), strict=True)})

    def totals(self) -> dict[str, int]:
        return {k: sum(c[k] for c in self.calls) for k in PRICE_PER_MTOK}

    def estimated_cost(self) -> float:
        t = self.totals()
        return sum(t[k] * PRICE_PER_MTOK[k] / 1_000_000 for k in PRICE_PER_MTOK)

    def summary(self) -> str:
        on_subscription = os.environ.get("LLM_PROVIDER", "").strip().lower() == "claude_cli"
        if not self.calls:
            if on_subscription:
                return "subscription (no API cost); token usage: n/a"
            return "no [LLM] usage lines captured (provider may not report usage; cost unknown)"
        t = self.totals()
        if on_subscription:
            return (
                f"subscription (no API cost); token usage: {len(self.calls)} LLM calls: "
                f"in={t['in']} out={t['out']} cache_read={t['cache_read']} cache_write={t['cache_write']}"
            )
        return (
            f"{len(self.calls)} LLM calls: in={t['in']} out={t['out']} "
            f"cache_read={t['cache_read']} cache_write={t['cache_write']} "
            f"-> ESTIMATED cost ${self.estimated_cost():.4f} "
            "(claude-sonnet-5-5 list prices: $2/M in, $10/M out, $0.20/M cache read, "
            "$2.50/M cache write; excludes TTS and judge calls on other models)"
        )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate + render one narrated video for one concept (SPENDS MONEY)."
    )
    p.add_argument("--paper", required=True, help="arXiv id already in the local DB, e.g. 1512.03385")
    p.add_argument("--concept", required=True, help='concept name, e.g. "Bottleneck Residual Block"')
    p.add_argument("--section-id", help="section id; default: the section of an existing visualization with this concept")
    p.add_argument(
        "--type",
        dest="viz_type",
        default="architecture",
        help="visualization type: equation|architecture|data_flow|algorithm|matrix|three_d (default architecture)",
    )
    p.add_argument("--out", default=None, help="output directory (default: media/test_runs/<viz_id>)")
    p.add_argument("--visual-narration", action="store_true", help="set VISUAL_NARRATION=1 for this process")
    return p


def _prepare_env(args: argparse.Namespace, out_dir: Path) -> None:
    """Load backend/.env, then pin the process to a local, DB-free, spend-contained setup.

    Must run BEFORE importing agents/rendering/db: they read env at import time.
    """
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")  # never overrides vars already in the environment
    if args.visual_narration:
        os.environ["VISUAL_NARRATION"] = "1"
    os.environ["RENDER_MODE"] = "local"  # no Modal
    os.environ["STORAGE_MODE"] = "local"  # no R2 upload
    os.environ["MEDIA_DIR"] = str(out_dir)  # process_visualization writes <viz_id>.mp4 here
    os.environ["ENABLE_VISUAL_QA"] = "0"  # no vision-judge calls on the render
    os.chdir(BACKEND_DIR)  # relative SQLite path + prompts
    sys.path.insert(0, str(BACKEND_DIR))


async def _load_paper_and_section(arxiv_id: str, concept: str, section_id: str | None):
    """(StructuredPaper, section_id) from the DB, read-only."""
    from db import queries
    from db.connection import async_session_maker
    from jobs.worker import _build_structured_paper_from_db

    async with async_session_maker() as db:
        paper = await queries.get_paper(db, arxiv_id)
        if paper is None:
            raise SystemExit(f"Paper {arxiv_id} is not in the database.")
        sections = sorted(paper.sections, key=lambda s: s.order_index or 0)
        if not section_id:
            wanted = concept.strip().lower()
            matches = [
                v.section_id
                for v in paper.visualizations
                if v.section_id and (v.concept or "").strip().lower() == wanted
            ]
            if not matches:
                listing = "\n".join(f"  {s.id}  {s.title}" for s in sections)
                raise SystemExit(
                    f"No existing visualization for concept {concept!r}; pass --section-id. Sections:\n{listing}"
                )
            section_id = matches[-1]
        if section_id not in {s.id for s in sections}:
            raise SystemExit(f"Section {section_id!r} not found in paper {arxiv_id}.")
        return _build_structured_paper_from_db(paper, sections), section_id


async def run(args: argparse.Namespace, out_dir: Path, viz_id: str) -> int:
    from agents import pipeline
    from agents.code_validator import CodeValidator
    from agents.manim_generator import ManimGenerator
    from agents.render_tester import RenderTester
    from agents.spatial_validator import SpatialValidator
    from agents.visual_narration import VISUAL_AID_MARKER
    from agents.visualization_planner import VisualizationPlanner
    from agents.voiceover_script_validator import VoiceoverScriptValidator
    from ingestion.text_normalize import normalize_display_text
    from models.generation import VisualizationCandidate, VisualizationType
    from rendering import process_visualization

    paper, section_id = await _load_paper_and_section(args.paper, args.concept, args.section_id)
    section = paper.get_section_by_id(section_id)
    candidate = VisualizationCandidate(
        section_id=section_id,
        concept_name=args.concept,
        concept_description=f"Illustrate {args.concept} as described in section '{section.title}'.",
        visualization_type=VisualizationType(args.viz_type),
        priority=5,
        context=normalize_display_text(section.content),
    )
    print(f"Paper: {paper.meta.title}\nSection: {section_id} ({section.title})\nviz_id: {viz_id}")

    # Same gate wiring as pipeline.generate_visualizations.
    planner = VisualizationPlanner()
    generator = ManimGenerator()
    validator = CodeValidator()
    spatial = SpatialValidator() if pipeline.ENABLE_SPATIAL_VALIDATION else None
    voice_validator = (
        VoiceoverScriptValidator(strict=pipeline.VOICE_QUALITY_STRICT) if pipeline.ENABLE_VOICEOVER else None
    )
    render_tester = RenderTester() if pipeline.ENABLE_RENDER_TESTING else None

    gate_trace: list[str] = []
    pipeline.metrics_hook = lambda gate, attempt, passed: gate_trace.append(
        f"attempt {attempt + 1}: {gate} {'PASS' if passed else 'FAIL'}"
    )

    viz = await pipeline.generate_single_visualization(
        candidate=candidate,
        paper=paper,
        planner=planner,
        generator=generator,
        validator=validator,
        spatial_validator=spatial,
        voiceover_script_validator=voice_validator,
        render_tester=render_tester,
        profile=pipeline.narrated_profile(),
    )
    print("\nGate trace:\n  " + "\n  ".join(gate_trace or ["(none)"]))
    if viz is None or not viz.manim_code:
        print("Generation failed (no code produced); see the log above.")
        return 1

    plan = json.loads(viz.storyboard) if isinstance(viz.storyboard, str) else viz.storyboard
    aid_scenes = [s for s in plan.get("scenes", []) if s.get("description", "").startswith(VISUAL_AID_MARKER)]
    code_path = out_dir / f"{viz_id}.py"
    code_path.write_text(viz.manim_code, encoding="utf-8")
    (out_dir / f"{viz_id}.plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(f"\nCode: {code_path}\nPlan ({len(aid_scenes)} VISUAL AID scenes, expected 3):")
    print(json.dumps(plan, indent=2))

    # viz was never persisted; rendering reuses only the id as the output file name.
    print("\nRendering (low quality, real TTS)...")
    await process_visualization(viz_id, viz.manim_code, quality="low_quality", kind="narrated")
    mp4 = out_dir / f"{viz_id}.mp4"
    print(f"MP4: {mp4}" if mp4.exists() else f"Render finished but {mp4} not found (check MEDIA_DIR).")
    return 0 if mp4.exists() else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    viz_id = f"test_{time.strftime('%Y%m%d_%H%M%S')}"
    out_dir = Path(args.out).resolve() if args.out else (BACKEND_DIR / "media" / "test_runs" / viz_id)

    _prepare_env(args, out_dir)
    if os.environ.get("VISUAL_NARRATION", "").strip().lower() not in ("1", "true"):
        print(
            "VISUAL_NARRATION is not set. Set VISUAL_NARRATION=1 or pass --visual-narration "
            "(without it this would generate a normal narrated video).",
            file=sys.stderr,
        )
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("agents.pipeline").setLevel(logging.INFO)
    usage = UsageCollector()
    base_logger = logging.getLogger("agents.base")
    base_logger.setLevel(logging.INFO)
    base_logger.addHandler(usage)

    try:
        code = asyncio.run(run(args, out_dir, viz_id))
    finally:
        print("\nToken/cost summary: " + usage.summary())
    return code


if __name__ == "__main__":
    sys.exit(main())
