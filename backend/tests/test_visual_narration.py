"""VISUAL_NARRATION flag: prompts unchanged when off, visual-aid blocks when on."""

import asyncio
from pathlib import Path

import pytest

from agents.manim_generator import ManimGenerator
from agents.visualization_planner import VisualizationPlanner
from models.generation import (
    Scene,
    VisualizationCandidate,
    VisualizationPlan,
    VisualizationType,
)

_PROMPTS = Path(__file__).parent.parent / "prompts"
_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _flag_off(monkeypatch):
    monkeypatch.delenv("VISUAL_NARRATION", raising=False)


def _plan():
    return VisualizationPlan(
        concept_name="Scaled Dot-Product Attention",
        visualization_type=VisualizationType.DATA_FLOW,
        duration_seconds=30,
        scenes=[Scene(order=1, description="beat", duration_seconds=10, transitions="Write", elements=["Text"])],
        narration_points=["p"],
    )


def _generator():
    g = ManimGenerator.__new__(ManimGenerator)
    g.prompt_template = (_PROMPTS / "manim_generator.md").read_text()
    g.silent_prompt_template = (_PROMPTS / "manim_generator_silent.md").read_text()
    g.system_prompt = "stable system"
    g.silent_example = "# silent example"
    g.examples = {VisualizationType.DATA_FLOW: "# plain example"}
    g.voiceover_examples = {VisualizationType.DATA_FLOW: "# voice example"}
    return g


def _narrated(g):
    return g._build_prompt(_plan(), True, "openai", "nova", "friendly_tutor", (30, 45))


def _planner():
    p = VisualizationPlanner.__new__(VisualizationPlanner)
    p.prompt_template = (_PROMPTS / "visualization_planner.md").read_text()
    return p


def _planner_prompt(p):
    candidate = VisualizationCandidate(
        section_id="s1",
        concept_name="Bottleneck Residual Block",
        concept_description="A 1x1, 3x3, 1x1 stack with identity shortcut.",
        visualization_type=VisualizationType.ARCHITECTURE,
        priority=5,
        context="Bottleneck context.",
    )
    return p._build_prompt(
        candidate,
        "The 1x1 layers reduce 256 channels to 64.",
        "Deep Residual Learning\n\nAbstract text.",
    )


# --- flag off: byte-identical -------------------------------------------------


def test_planner_prompt_unchanged_when_off():
    assert _planner_prompt(_planner()) == (_FIXTURES / "visualization_planner_prompt.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("value", ["", "0", "false", "no"])
def test_falsy_flag_values_are_off(monkeypatch, value):
    monkeypatch.setenv("VISUAL_NARRATION", value)
    assert _planner_prompt(_planner()) == (_FIXTURES / "visualization_planner_prompt.txt").read_text(encoding="utf-8")
    assert _narrated(_generator()) == (_FIXTURES / "manim_prompt_narrated_voice.txt").read_text(encoding="utf-8")


def test_generator_prompt_unchanged_when_off():
    g = _generator()
    assert _narrated(g) == (_FIXTURES / "manim_prompt_narrated_voice.txt").read_text(encoding="utf-8")
    plain = g._build_prompt(_plan(), False, "openai", "nova", "friendly_tutor", (30, 45))
    assert plain == (_FIXTURES / "manim_prompt_narrated_plain.txt").read_text(encoding="utf-8")


# --- flag on ------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "TRUE"])
def test_planner_prompt_has_visual_aid_block_when_on(monkeypatch, value):
    monkeypatch.setenv("VISUAL_NARRATION", value)
    p = _planner()
    prompt = _planner_prompt(p)
    assert "EXACTLY 3" in prompt
    assert '"VISUAL AID: "' in prompt
    assert "proportional" in prompt
    # Center stage built from phrase-sized beats, 3 corner analogies, BR recap.
    assert "CENTER STAGE" in prompt
    assert "ONE narration phrase of 4-8 words + ONE visible change" in prompt
    for corner in ("TL", "TR", "BL", "BR"):
        assert corner in prompt
    assert "Recap (BR)" in prompt
    assert "everyday analogy" in prompt
    assert "2-5 second" in prompt
    # Original content intact, block sits before the output format, template restored.
    assert "## Narration Points Guidance" in prompt
    assert prompt.index("Visual Aid Moments") < prompt.index("## Output Format (JSON only)")
    assert "Visual Aid Moments" not in p.prompt_template


def test_generator_prompt_has_show_dont_label_block_when_on(monkeypatch):
    monkeypatch.setenv("VISUAL_NARRATION", "1")
    g = _generator()
    prompt = _narrated(g)
    assert "Show, Don't Label" in prompt
    assert '"VISUAL AID:"' in prompt
    assert "proportional" in prompt
    assert "3 words or fewer" in prompt
    # Existing narration, spatial and LaTeX rules still present.
    assert "with self.voiceover(text=" in prompt
    assert "run_time=tracker.duration" in prompt
    assert "Spatial Quality Requirements" in prompt
    assert "LaTeX and MathTex Safety" in prompt
    assert prompt.index("Show, Don't Label") < prompt.index("## Output Contract")
    assert "Show, Don't Label" not in g.prompt_template


def test_silent_and_plain_prompts_unaffected_when_on(monkeypatch):
    g = _generator()
    silent_off = g._build_prompt(_plan(), False, "openai", "nova", "friendly_tutor", (5, 10), silent_loop=True)
    plain_off = g._build_prompt(_plan(), False, "openai", "nova", "friendly_tutor", (30, 45))
    monkeypatch.setenv("VISUAL_NARRATION", "1")
    assert g._build_prompt(_plan(), False, "openai", "nova", "friendly_tutor", (5, 10), silent_loop=True) == silent_off
    assert g._build_prompt(_plan(), False, "openai", "nova", "friendly_tutor", (30, 45)) == plain_off


def _retry_prompt(g):
    captured = {}

    async def fake_enrich(plan):
        return ""

    async def fake_call(prompt, system_prompt=None, system_suffix=""):
        captured["prompt"] = prompt
        return "from manim import *\nclass A(Scene):\n    pass\n"

    g._live_docs_suffix = fake_enrich
    g._call_llm = fake_call
    asyncio.run(g.run_with_feedback(plan=_plan(), previous_code="x", error_message="bad"))
    return captured["prompt"]


def test_retry_prompt_keeps_block_when_on(monkeypatch):
    monkeypatch.setenv("VISUAL_NARRATION", "1")
    prompt = _retry_prompt(_generator())
    assert "Show, Don't Label" in prompt
    assert "Previous Attempt Failed" in prompt
    assert 'Keep every "VISUAL AID:" scene DRAWN' in prompt
    assert "Keep strict narration quality" in prompt


def test_retry_prompt_unchanged_when_off():
    prompt = _retry_prompt(_generator())
    assert "Show, Don't Label" not in prompt
    assert "VISUAL AID" not in prompt


def test_generator_prompt_has_layout_and_contrast_rules_when_on(monkeypatch):
    monkeypatch.setenv("VISUAL_NARRATION", "1")
    prompt = _narrated(_generator())
    # Narration phrasing matches the narration check's hard rules.
    assert 'never begin a phrase with "Display", "Show"' in prompt
    assert "Every phrase has at least 4 words" in prompt
    # Tags stay clear of outlines and borders.
    assert "Tags never touch lines" in prompt
    # Opening: title alone, cleared before the diagram, diagram below it.
    assert "appears ALONE" in prompt
    assert ".animate.to_edge(UP).scale(" in prompt
    assert "BEFORE any diagram element appears" in prompt
    assert "below y = 2.5" in prompt
    # Tag contrast.
    assert "high-contrast color" in prompt
    assert "Never use `GREY`" in prompt
    assert "opacity below 0.8" in prompt
    assert "`font_size` of at least 28" in prompt
    assert "buff of 0.3 or more" in prompt
    # Final composition.
    assert "Final composition" in prompt
    assert "simplified block icons at legible size" in prompt
    assert "Do NOT `FadeOut` the center stage" in prompt


def test_layout_and_contrast_rules_absent_when_off():
    assert "appears ALONE" not in _narrated(_generator())


def test_generator_prompt_has_layout_pacing_and_budget_when_on(monkeypatch):
    monkeypatch.setenv("VISUAL_NARRATION", "1")
    prompt = _narrated(_generator())
    # Layout convention with concrete coordinates.
    assert "14.2 wide x 8 tall" in prompt
    assert "7.0 x 3.8" in prompt
    assert "3.0 x 1.5" in prompt
    assert "`UL`, `UR`, `DL`, `DR`" in prompt
    assert "flush with the four corners of the base safe area" in prompt
    assert "named constants and helpers" in prompt
    assert "never cleared" in prompt.lower() or "It is never cleared" in prompt
    assert "Indicate" in prompt and "Circumscribe" in prompt
    assert "shrinks to a small resting icon" in prompt
    # Pacing.
    assert "4-8 words" in prompt
    assert "Never narrate over a still frame" in prompt
    # Text budget numbers.
    assert "3 words or fewer" in prompt
    assert "15 tag words or fewer" in prompt
    assert "Paragraph" in prompt
    # Vocabulary.
    assert "funnels" in prompt and "no external images" in prompt


def test_few_shot_snippet_passes_code_validator():
    import re

    from agents.code_validator import CodeValidator
    from agents.frame_check import check_frames
    from agents.text_budget import check_text_budget

    block = (_PROMPTS / "visual_narration_generator.md").read_text(encoding="utf-8")
    snippet = re.search(r"```python\n(.*?)```", block, re.S).group(1)
    body = "\n".join("        " + ln for ln in snippet.splitlines())
    code = (
        "from manim import *\nfrom manim_voiceover import VoiceoverScene\n"
        "class Demo(VoiceoverScene):\n    def construct(self):\n" + body + "\n"
    )
    result = CodeValidator().validate(code)
    assert result.is_valid and not result.needs_regeneration, result.issues_found
    assert check_text_budget(result.code).passed
    assert check_frames(result.code).passed


def _snippet():
    import re

    block = (_PROMPTS / "visual_narration_generator.md").read_text(encoding="utf-8")
    return re.search(r"```python\n(.*?)```", block, re.S).group(1)


def test_snippet_layout_constants_inside_safe_area_and_non_overlapping():
    import re

    ns: dict = {}
    for line in _snippet().splitlines():
        if re.fullmatch(r"[A-Z_]+(, [A-Z_]+)* = [\d.]+(, [\d.]+)*", line):
            exec(line, ns)  # numeric constant lines from our own prompt
    safe_x, safe_y = ns["SAFE_X"], ns["SAFE_Y"]
    assert (safe_x, safe_y) == (6.0, 3.5)  # base prompt / SpatialValidator safe area
    cw, ch, w, h = ns["CORNER_W"], ns["CORNER_H"], ns["CENTER_W"], ns["CENTER_H"]
    corner = (safe_x - cw, safe_x, safe_y - ch, safe_y)  # top-right box: x0, x1, y0, y1
    center = (-w / 2, w / 2, -h / 2, h / 2)
    assert center[1] <= safe_x and center[3] <= safe_y
    x_overlap = corner[0] < center[1] and center[0] < corner[1]
    y_overlap = corner[2] < center[3] and center[2] < corner[3]
    assert not (x_overlap and y_overlap)
    # Opposite corners on the same side never meet either.
    assert 2 * cw < 2 * safe_x and 2 * ch < 2 * safe_y


def test_snippet_dot_respects_minimum_size():
    import re

    radii = [float(r) for r in re.findall(r"Dot\(radius=([\d.]+)", _snippet())]
    assert radii and all(2 * r >= 0.25 for r in radii)


def test_generator_block_states_overrides_and_render_defect_rules(monkeypatch):
    monkeypatch.setenv("VISUAL_NARRATION", "1")
    prompt = _narrated(_generator())
    assert "overrides the base spatial rules" in prompt
    assert "never FadeOut or clear the center stage" in prompt
    assert "`buff` is at least 0.3 everywhere" in prompt
    assert "counts TAGS only" in prompt
    assert "`Transform` / `ReplacementTransform` its label too" in prompt
    assert "never drawn twice on top of itself" in prompt
    assert "recap lives ONLY in the BR corner" in prompt
    assert "nothing new is added over the center stage except highlights" in prompt


def test_prompts_ban_frames_when_on(monkeypatch):
    monkeypatch.setenv("VISUAL_NARRATION", "1")
    gen = _narrated(_generator())
    assert "No frames (a static check enforces this)" in gen
    assert "Never construct a `SurroundingRectangle`" in gen
    assert "If the plan asks for an outline, frame, dashed box or placeholder, leave it out" in gen
    # The old rule handed out a frame recipe.
    assert "SurroundingRectangle(..., buff=0.4" not in gen
    plan = _planner_prompt(_planner())
    assert "No frames: never plan an outline, frame, dashed box, placeholder" in plan
    assert "The first scene draws the real input shape itself" in plan
    assert "Never list `SurroundingRectangle` or `DashedVMobject` in `elements`" in plan
