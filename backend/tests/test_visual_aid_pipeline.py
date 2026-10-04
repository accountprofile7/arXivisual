"""Generation profiles and generate_visual_aids (mocks only, no network)."""

import asyncio

import pytest
from pydantic import ValidationError

from agents import pipeline
from models.generation import (
    GeneratedCode,
    Scene,
    ValidatorOutput,
    VisualAidCandidate,
    VisualizationCandidate,
    VisualizationPlan,
    VisualizationStatus,
    VisualizationType,
)
from models.paper import ArxivPaperMeta, Section, StructuredPaper
from models.spatial import SpatialValidatorOutput


def _aid(section_id="s1", name="Attention", idx=1, beats=("a", "b", "c")) -> VisualAidCandidate:
    return VisualAidCandidate(
        section_id=section_id,
        concept_name=name,
        concept_description="desc",
        visualization_type=VisualizationType.DATA_FLOW,
        paragraph_index=idx,
        beats=list(beats),
        context="ctx",
    )


def _code(tag="x") -> GeneratedCode:
    return GeneratedCode(code=f"# {tag}\nfrom manim import *\n", scene_class_name="Viz")


class _Paper:
    def get_section_by_id(self, _sid):
        return None

    def get_context(self):
        return "context"


class _ExplodingPlanner:
    async def run(self, **_kw):
        raise AssertionError("aids must not call the planner")


class _Generator:
    def __init__(self):
        self.run_kwargs: list[dict] = []
        self.feedback_kwargs: list[dict] = []

    async def run(self, **kw):
        self.run_kwargs.append(kw)
        return _code("first")

    async def run_with_feedback(self, **kw):
        self.feedback_kwargs.append(kw)
        return _code("retry")


class _Validator:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = 0

    def validate(self, code):
        self.calls += 1
        return ValidatorOutput(
            is_valid=self.ok,
            code=code,
            issues_found=[] if self.ok else ["bad"],
            needs_regeneration=not self.ok,
        )


def _single(candidate, *, validator, generator=None, planner=None, **kw):
    return asyncio.run(
        pipeline.generate_single_visualization(
            candidate=candidate,
            paper=_Paper(),
            planner=planner or _ExplodingPlanner(),
            generator=generator or _Generator(),
            validator=validator,
            **kw,
        )
    )


# --- profiles ----------------------------------------------------------------


def test_narrated_profile_reproduces_current_constants():
    p = pipeline.NARRATED
    assert p.voiceover is pipeline.ENABLE_VOICEOVER
    assert p.target_duration_seconds == pipeline.VOICEOVER_TARGET_DURATION_SECONDS == (30, 45)
    assert p.max_attempts == pipeline.MAX_RETRIES + pipeline.VOICE_QUALITY_RETRIES == 5
    assert p.gate_prefix == ""
    assert p.fail_behavior == pipeline.VOICE_FAIL_BEHAVIOR == "return_silent"
    assert p.kind == "narrated"
    assert p.silent_loop is False


def test_visual_aid_profile():
    p = pipeline.VISUAL_AID
    assert (p.voiceover, p.target_duration_seconds, p.max_attempts) == (False, (5, 10), 3)
    assert (p.gate_prefix, p.fail_behavior, p.kind, p.silent_loop) == (
        "aid:",
        "drop",
        "visual_aid",
        True,
    )
    with pytest.raises(AttributeError):
        p.max_attempts = 9  # frozen


def test_default_profile_follows_module_constants(monkeypatch):
    """Existing callers/tests monkeypatch the constants; the default must honor them."""
    monkeypatch.setattr(pipeline, "ENABLE_VOICEOVER", True)
    assert pipeline.narrated_profile().max_attempts == 5
    monkeypatch.setattr(pipeline, "ENABLE_VOICEOVER", False)
    assert pipeline.narrated_profile().max_attempts == 3 == pipeline.MAX_RETRIES


def test_narrated_all_fail_still_returns_silent_after_old_attempt_count(monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_VOICEOVER", True)
    gen, val = _Generator(), _Validator(ok=False)

    class _Plan:
        async def run(self, **_kw):
            return VisualizationPlan(
                concept_name="C",
                visualization_type=VisualizationType.DATA_FLOW,
                duration_seconds=30,
                scenes=[],
            )

    cand = VisualizationCandidate(
        section_id="s1",
        concept_name="C",
        concept_description="d",
        visualization_type=VisualizationType.DATA_FLOW,
        priority=3,
        context="c",
    )
    viz = _single(cand, validator=val, generator=gen, planner=_Plan())
    assert val.calls == 5
    assert viz is not None and viz.status == VisualizationStatus.PENDING
    assert viz.kind == "narrated" and viz.anchor_paragraph is None
    assert gen.run_kwargs[0]["voiceover_enabled"] is True
    assert gen.run_kwargs[0]["target_duration_seconds"] == (30, 45)
    assert "silent_loop" not in gen.run_kwargs[0]


# --- aid plan ----------------------------------------------------------------


def test_aid_plan_one_scene_per_beat_within_5_10():
    plan = pipeline.build_aid_plan(_aid(beats=("one", "two", "three")))
    assert [s.description for s in plan.scenes] == ["one", "two", "three"]
    assert [s.order for s in plan.scenes] == [1, 2, 3]
    assert 5 <= plan.duration_seconds <= 10
    assert plan.concept_name == "Attention"
    assert plan.visualization_type == VisualizationType.DATA_FLOW


@pytest.mark.parametrize("beats", [(), ("only",), ("a", "b", "c", "d")])
def test_aid_plan_duration_bounds_for_any_beat_count(beats):
    plan = pipeline.build_aid_plan(_aid(beats=beats))
    assert 5 <= plan.duration_seconds <= 10
    assert len(plan.scenes) == max(1, len(beats))


def test_narrated_plan_still_rejects_short_durations():
    with pytest.raises(ValidationError):
        VisualizationPlan(
            concept_name="x",
            visualization_type=VisualizationType.EQUATION,
            duration_seconds=8,
            scenes=[Scene(order=1, description="d", duration_seconds=8, transitions="t")],
        )


# --- aid generation ----------------------------------------------------------


def test_aid_generation_is_silent_unplanned_and_tagged():
    gen = _Generator()
    viz = _single(
        _aid(section_id="sec9", idx=4),
        validator=_Validator(),
        generator=gen,
        profile=pipeline.VISUAL_AID,
    )
    assert viz is not None
    assert (viz.kind, viz.anchor_paragraph, viz.section_id) == ("visual_aid", 4, "sec9")
    kw = gen.run_kwargs[0]
    assert kw["voiceover_enabled"] is False
    assert kw["target_duration_seconds"] == (5, 10)
    assert kw["silent_loop"] is True
    assert 5 <= kw["plan"].duration_seconds <= 10


def test_aid_failure_is_dropped_after_three_attempts():
    gen, val = _Generator(), _Validator(ok=False)
    viz = _single(_aid(), validator=val, generator=gen, profile=pipeline.VISUAL_AID)
    assert viz is None
    assert val.calls == 3
    assert len(gen.feedback_kwargs) == 2
    assert all(k["silent_loop"] is True for k in gen.feedback_kwargs)


def test_aid_gates_are_prefixed(monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline, "metrics_hook", lambda g, a, p: seen.append((g, a, p)))

    class _Spatial:
        def validate(self, code):
            return SpatialValidatorOutput(has_spatial_issues=False, needs_regeneration=False)

    class _Render:
        async def test_render(self, code):
            class R:
                success = True
                error_type = None
                error_message = None

            return R()

    viz = _single(
        _aid(),
        validator=_Validator(),
        spatial_validator=_Spatial(),
        render_tester=_Render(),
        profile=pipeline.VISUAL_AID,
    )
    assert viz is not None
    assert seen == [
        ("aid:code_validator", 0, True),
        ("aid:spatial_validator", 0, True),
        ("aid:render_tester", 0, True),
    ]


# --- generate_visual_aids ----------------------------------------------------


def _paper(*titles_ids):
    return StructuredPaper(
        meta=ArxivPaperMeta(
            arxiv_id="2401.00001",
            title="T",
            authors=["A"],
            abstract="abs",
            pdf_url="https://arxiv.org/pdf/2401.00001",
        ),
        sections=[Section(id=sid, title=title, content="x" * 200) for title, sid in titles_ids]
        + [Section(id="short", title="Tiny", content="short")],
    )


class _Analyzer:
    calls: list[dict] = []

    def __init__(self, per_section):
        self.per_section = per_section

    async def run(self, paper_title, paper_abstract, section, *, max_aids=3, skip_concepts=()):
        _Analyzer.calls.append(
            {"section": section.id, "max_aids": max_aids, "skip": tuple(skip_concepts)}
        )
        if section.id == "boom":
            raise RuntimeError("analysis failed")
        return [_aid(section.id, f"{section.id}-c{i}", i) for i in range(self.per_section)]


@pytest.fixture
def aid_env(monkeypatch):
    _Analyzer.calls = []
    made = {}

    def install(per_section=5, fail=()):
        made["profiles"] = []
        monkeypatch.setattr(pipeline, "VisualAidAnalyzer", lambda: _Analyzer(per_section))
        for name in ("ManimGenerator", "CodeValidator", "SpatialValidator", "RenderTester"):
            monkeypatch.setattr(pipeline, name, object)

        async def fake_single(candidate, paper, planner, generator, validator, *a, profile=None, **kw):
            made["profiles"].append(profile)
            if candidate.concept_name in fail:
                return None
            return pipeline.Visualization(
                id=f"v-{candidate.concept_name}",
                section_id=candidate.section_id,
                concept=candidate.concept_name,
                storyboard="{}",
                manim_code="c",
                kind=profile.kind,
                anchor_paragraph=candidate.paragraph_index,
            )

        monkeypatch.setattr(pipeline, "generate_single_visualization", fake_single)
        return made

    return install


def test_generate_visual_aids_caps_per_section_and_total(aid_env):
    made = aid_env(per_section=5)
    paper = _paper(("Intro", "s1"), ("Method", "s2"), ("References", "refs"), ("Results", "s3"))
    out = asyncio.run(pipeline.generate_visual_aids(paper, max_per_section=2, max_total=5))
    # references + short sections are skipped; 2 per section; total cap 5 in section order
    assert [v.concept for v in out] == ["s1-c0", "s1-c1", "s2-c0", "s2-c1", "s3-c0"]
    assert {c["section"] for c in _Analyzer.calls} == {"s1", "s2", "s3"}
    assert all(c["max_aids"] == 2 for c in _Analyzer.calls)
    assert all(p is pipeline.VISUAL_AID for p in made["profiles"])
    assert all(v.kind == "visual_aid" for v in out)
    assert [v.anchor_paragraph for v in out] == [0, 1, 0, 1, 0]


def test_generate_visual_aids_passes_skip_concepts_and_drops_failures(aid_env):
    aid_env(per_section=2, fail={"s1-c0"})
    out = asyncio.run(
        pipeline.generate_visual_aids(_paper(("Intro", "s1")), skip_concepts={"Teacher"})
    )
    assert [v.concept for v in out] == ["s1-c1"]
    assert _Analyzer.calls[0]["skip"] == ("Teacher",)


def test_generate_visual_aids_survives_analyzer_failure(aid_env):
    aid_env(per_section=1)
    out = asyncio.run(pipeline.generate_visual_aids(_paper(("Intro", "boom"), ("Method", "s2"))))
    assert [v.concept for v in out] == ["s2-c0"]


def test_generate_visual_aids_checkpoint_callback_and_error(aid_env):
    aid_env(per_section=2)
    got = []

    async def cb(v):
        got.append(v.concept)

    out = asyncio.run(pipeline.generate_visual_aids(_paper(("Intro", "s1")), on_visualization=cb))
    assert sorted(got) == sorted(v.concept for v in out) == ["s1-c0", "s1-c1"]

    async def bad(_v):
        raise OSError("db down")

    with pytest.raises(pipeline.CheckpointError):
        asyncio.run(pipeline.generate_visual_aids(_paper(("Intro", "s1")), on_visualization=bad))


def test_visual_aid_env_defaults():
    assert pipeline.ENABLE_VISUAL_AIDS is False
    assert pipeline.VISUAL_AIDS_PER_SECTION == 3
    assert pipeline.MAX_VISUAL_AIDS == 15


def test_env_parsing(monkeypatch):
    monkeypatch.setenv("ENABLE_VISUAL_AIDS", "TRUE")
    assert pipeline._env_flag("ENABLE_VISUAL_AIDS") is True
    monkeypatch.setenv("ENABLE_VISUAL_AIDS", "0")
    assert pipeline._env_flag("ENABLE_VISUAL_AIDS") is False
    monkeypatch.setenv("MAX_VISUAL_AIDS", "junk")
    assert pipeline._env_int("MAX_VISUAL_AIDS", 15) == 15
    monkeypatch.setenv("MAX_VISUAL_AIDS", "7")
    assert pipeline._env_int("MAX_VISUAL_AIDS", 15) == 7
