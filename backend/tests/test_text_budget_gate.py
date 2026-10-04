"""text_budget gate wiring in generate_single_visualization (hermetic fakes)."""

import asyncio

from agents import pipeline
from models.generation import (
    GeneratedCode,
    Scene,
    ValidatorOutput,
    VisualizationCandidate,
    VisualizationPlan,
    VisualizationType,
)

BAD = (
    "from manim import *\nclass Viz(Scene):\n    def construct(self):\n"
    '        self.add(Text("this is a whole sentence of words"))\n'
)
GOOD = "from manim import *\nclass Viz(Scene):\n    def construct(self):\n        self.add(Text('256'))\n"


def _code(code):
    return GeneratedCode(
        code=code,
        scene_class_name="Viz",
        dependencies=["manim"],
        voiceover_enabled=True,
        narration_lines=[],
        narration_beats=[],
    )


class _Paper:
    def get_section_by_id(self, _sid):
        return None

    def get_context(self):
        return "context"


class _Planner:
    async def run(self, candidate, full_section_content, paper_context):
        return VisualizationPlan(
            concept_name="c",
            visualization_type=VisualizationType.DATA_FLOW,
            duration_seconds=30,
            scenes=[Scene(order=1, description="beat", duration_seconds=10, transitions="Write", elements=["Text"])],
            narration_points=[],
        )


class _Generator:
    def __init__(self, codes):
        self.codes = list(codes)
        self.feedback: list[str] = []
        self.runs = 0

    async def run(self, **_kw):
        self.runs += 1
        return _code(self.codes.pop(0))

    async def run_with_feedback(self, plan, previous_code, error_message, **_kw):
        self.feedback.append(error_message)
        self.runs += 1
        return _code(self.codes.pop(0))


class _Validator:
    def validate(self, code):
        return ValidatorOutput(is_valid=True, code=code, issues_found=[], needs_regeneration=False)


def _run(monkeypatch, codes, narration):
    monkeypatch.setattr(pipeline, "ENABLE_VOICEOVER", True)
    if narration:
        monkeypatch.setenv("VISUAL_NARRATION", "1")
    else:
        monkeypatch.delenv("VISUAL_NARRATION", raising=False)
    gates: list[tuple[str, int, bool]] = []
    monkeypatch.setattr(pipeline, "metrics_hook", lambda g, a, p: gates.append((g, a, p)))
    gen = _Generator(codes)
    candidate = VisualizationCandidate(
        section_id="s",
        concept_name="c",
        concept_description="d",
        visualization_type=VisualizationType.DATA_FLOW,
        priority=5,
        context="x",
    )
    asyncio.run(
        pipeline.generate_single_visualization(
            candidate=candidate,
            paper=_Paper(),
            planner=_Planner(),
            generator=gen,
            validator=_Validator(),
        )
    )
    return gen, gates


def test_flag_on_violation_retries_with_budget_feedback(monkeypatch):
    gen, gates = _run(monkeypatch, [BAD, GOOD], narration=True)
    assert gen.runs == 2
    assert "TEXT BUDGET EXCEEDED" in gen.feedback[0]
    assert "this is a whole sentence of words" in gen.feedback[0]
    assert "keep tags <=3 words" in gen.feedback[0]
    assert ("text_budget", 0, False) in gates
    assert ("text_budget", 1, True) in gates


def test_flag_off_gate_never_runs(monkeypatch):
    gen, gates = _run(monkeypatch, [BAD], narration=False)
    assert gen.runs == 1
    assert all(g != "text_budget" for g, _, _ in gates)
    assert [g for g, _, _ in gates] == ["code_validator"]


def test_silent_aid_profile_never_runs_gate(monkeypatch):
    from models.generation import VisualAidCandidate

    monkeypatch.setenv("VISUAL_NARRATION", "1")
    gates: list[str] = []
    monkeypatch.setattr(pipeline, "metrics_hook", lambda g, a, p: gates.append(g))
    gen = _Generator([BAD])
    aid = VisualAidCandidate(
        section_id="s",
        concept_name="c",
        concept_description="d",
        visualization_type=VisualizationType.DATA_FLOW,
        paragraph_index=0,
        beats=["b1", "b2"],
        context="ctx",
    )
    asyncio.run(
        pipeline.generate_single_visualization(
            candidate=aid,
            paper=_Paper(),
            planner=None,
            generator=gen,
            validator=_Validator(),
            profile=pipeline.VISUAL_AID,
        )
    )
    assert gen.runs == 1
    assert not any(g.endswith("text_budget") for g in gates)


class _Render:
    def __init__(self, success=True):
        self.success = success
        self.calls = 0

    async def test_render(self, code):
        from agents.render_tester import RenderTestOutput

        self.calls += 1
        if self.success:
            return RenderTestOutput(success=True)
        return RenderTestOutput(success=False, error_type="RuntimeError", error_message="boom")


def _run_with_render(monkeypatch, codes, render, max_attempts):
    import dataclasses

    monkeypatch.setattr(pipeline, "ENABLE_VOICEOVER", True)
    monkeypatch.setenv("VISUAL_NARRATION", "1")
    gates: list[tuple[str, int, bool]] = []
    monkeypatch.setattr(pipeline, "metrics_hook", lambda g, a, p: gates.append((g, a, p)))
    gen = _Generator(codes)
    candidate = VisualizationCandidate(
        section_id="s",
        concept_name="c",
        concept_description="d",
        visualization_type=VisualizationType.DATA_FLOW,
        priority=5,
        context="x",
    )
    result = asyncio.run(
        pipeline.generate_single_visualization(
            candidate=candidate,
            paper=_Paper(),
            planner=_Planner(),
            generator=gen,
            validator=_Validator(),
            render_tester=render,
            profile=dataclasses.replace(pipeline.narrated_profile(), max_attempts=max_attempts),
        )
    )
    return result, gen, gates


def test_last_attempt_over_budget_still_runs_downstream_gates(monkeypatch):
    render = _Render(success=True)
    result, gen, gates = _run_with_render(monkeypatch, [BAD], render, max_attempts=1)
    assert render.calls == 1  # render gate ran despite the budget miss
    assert ("render_tester", 0, True) in gates
    assert result is not None and result.manim_code == BAD
    assert gen.runs == 1


def test_budget_miss_with_attempts_left_runs_render_then_retries(monkeypatch):
    render = _Render(success=True)
    _, gen, _ = _run_with_render(monkeypatch, [BAD, GOOD], render, max_attempts=2)
    assert render.calls == 2
    assert gen.runs == 2
    assert "TEXT BUDGET EXCEEDED" in gen.feedback[0]


def test_budget_and_render_feedback_are_combined(monkeypatch):
    render = _Render(success=False)
    _, gen, _ = _run_with_render(monkeypatch, [BAD, GOOD], render, max_attempts=2)
    assert "TEXT BUDGET EXCEEDED" in gen.feedback[0]
    assert "boom" in gen.feedback[0]
