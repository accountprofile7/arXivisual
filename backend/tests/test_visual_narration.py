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
    # Opening: title alone, cleared before the diagram, diagram below it.
    assert "title appears ALONE" in prompt
    assert ".animate.to_edge(UP).scale(" in prompt
    assert "BEFORE any diagram element appears" in prompt
    assert "below y = 2.5" in prompt
    # Tag contrast.
    assert "high-contrast color" in prompt
    assert "Never use `GREY`" in prompt
    assert "opacity below 0.8" in prompt
    assert "`font_size` of at least 28" in prompt
    assert "buff of 0.15 or more" in prompt
    # Final composition.
    assert "between 40% and 85% of the frame width" in prompt
    assert "centered vertically in the safe area" in prompt
    assert "simplified block icons at legible size" in prompt
    assert "`FadeOut` all previous content" in prompt


def test_layout_and_contrast_rules_absent_when_off():
    assert "title appears ALONE" not in _narrated(_generator())
