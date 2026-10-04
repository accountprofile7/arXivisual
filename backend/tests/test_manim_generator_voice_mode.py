from agents.manim_generator import ManimGenerator
from models.generation import VisualizationType


def test_extract_scene_class_name_supports_voiceover_scene():
    generator = ManimGenerator.__new__(ManimGenerator)
    code = """
from manim import *
from manim_voiceover import VoiceoverScene

class VoiceAligned(VoiceoverScene):
    def construct(self):
        pass
"""
    assert generator._extract_scene_class_name(code) == "VoiceAligned"


def test_extract_narration_lines_and_beats():
    generator = ManimGenerator.__new__(ManimGenerator)
    code = """
# Beat 1: intro
# Beat 2: scoring
with self.voiceover(text=\"Queries compare keys to score relevance across tokens in context-rich attention maps.\") as tracker:
    self.play(Create(arrow), run_time=tracker.duration)
# Beat 3: weighting
with self.voiceover(text=\"Softmax normalizes similarities so weighted values form each contextual representation cleanly.\") as tracker:
    self.play(Write(eq), run_time=tracker.duration)
"""
    lines = generator._extract_narration_lines(code)
    beats = generator._extract_beat_labels(code)

    assert len(lines) == 2
    assert lines[0].startswith("Queries compare keys")
    assert beats == ["# Beat 1: intro", "# Beat 2: scoring", "# Beat 3: weighting"]


def test_selects_voiceover_examples_when_enabled():
    generator = ManimGenerator.__new__(ManimGenerator)
    generator.examples = {VisualizationType.DATA_FLOW: "plain_example"}
    generator.voiceover_examples = {VisualizationType.DATA_FLOW: "voiceover_example"}

    selected_voice = generator._get_example_for_type(VisualizationType.DATA_FLOW, voiceover_enabled=True)
    selected_plain = generator._get_example_for_type(VisualizationType.DATA_FLOW, voiceover_enabled=False)

    assert selected_voice == "voiceover_example"
    assert selected_plain == "plain_example"


# --- silent visual-aid prompt ------------------------------------------------

import asyncio
from pathlib import Path

from models.generation import Scene, VisualizationPlan

_PROMPTS = Path(__file__).parent.parent / "prompts"
_FIXTURES = Path(__file__).parent / "fixtures"


def _plan():
    return VisualizationPlan(
        concept_name="Scaled Dot-Product Attention",
        visualization_type=VisualizationType.DATA_FLOW,
        duration_seconds=30,
        scenes=[Scene(order=1, description="beat", duration_seconds=10, transitions="Write", elements=["Text"])],
        narration_points=["p"],
    )


def _prompt_generator():
    g = ManimGenerator.__new__(ManimGenerator)
    g.prompt_template = (_PROMPTS / "manim_generator.md").read_text()
    g.silent_prompt_template = (_PROMPTS / "manim_generator_silent.md").read_text()
    g.examples = {VisualizationType.DATA_FLOW: "# plain example"}
    g.voiceover_examples = {VisualizationType.DATA_FLOW: "# voice example"}
    return g


def test_narrated_prompt_unchanged_voice_mode():
    prompt = _prompt_generator()._build_prompt(_plan(), True, "openai", "nova", "friendly_tutor", (30, 45))
    assert prompt == (_FIXTURES / "manim_prompt_narrated_voice.txt").read_text(encoding="utf-8")


def test_narrated_prompt_unchanged_plain_mode():
    prompt = _prompt_generator()._build_prompt(_plan(), False, "openai", "nova", "friendly_tutor", (30, 45))
    assert prompt == (_FIXTURES / "manim_prompt_narrated_plain.txt").read_text(encoding="utf-8")


def _silent_prompt():
    return _prompt_generator()._build_prompt(
        _plan(), False, "openai", "nova", "friendly_tutor", (5, 10), silent_loop=True
    )


def test_silent_prompt_has_no_voice_wording_and_has_loop_rules():
    prompt = _silent_prompt()
    lowered = prompt.lower()
    for banned in ("voiceover", "narrat", "tts", "voice"):
        assert banned not in lowered
    assert "5-10 seconds" in prompt
    assert "plain `Scene`" in prompt
    assert "LAST frame must match the FIRST frame" in prompt
    assert "title card" in lowered
    assert "[-6, 6]" in prompt
    assert "# plain example" in prompt


def test_silent_feedback_prompt_has_no_narration_wording():
    g = _prompt_generator()
    captured = {}

    async def fake_enrich(plan):
        return "system"

    async def fake_call(prompt, system_prompt=None):
        captured["prompt"] = prompt
        return "```python\nfrom manim import *\nclass A(Scene):\n    def construct(self):\n        pass\n```"

    g._enrich_system_prompt_with_live_docs = fake_enrich
    g._call_llm = fake_call
    result = asyncio.run(
        g.run_with_feedback(
            plan=_plan(),
            previous_code="x = 1",
            error_message="bad",
            voiceover_enabled=False,
            target_duration_seconds=(5, 10),
            silent_loop=True,
        )
    )
    lowered = captured["prompt"].lower()
    assert "narrat" not in lowered and "voice" not in lowered
    assert "previous attempt failed" in lowered
    assert result.voiceover_enabled is False


def test_narrated_feedback_prompt_still_mentions_narration():
    g = _prompt_generator()
    captured = {}

    async def fake_enrich(plan):
        return "system"

    async def fake_call(prompt, system_prompt=None):
        captured["prompt"] = prompt
        return "from manim import *\nclass A(Scene):\n    pass\n"

    g._enrich_system_prompt_with_live_docs = fake_enrich
    g._call_llm = fake_call
    asyncio.run(g.run_with_feedback(plan=_plan(), previous_code="x", error_message="bad"))
    assert "Keep strict narration quality" in captured["prompt"]
