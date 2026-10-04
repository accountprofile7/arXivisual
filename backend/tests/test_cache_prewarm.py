"""Cache warm-up before the concurrent generator burst (fake Anthropic client, no network)."""

from types import SimpleNamespace

from agents import base, pipeline
from agents import manim_generator as mg
from models.generation import (
    Scene,
    VisualAidCandidate,
    Visualization,
    VisualizationCandidate,
    VisualizationPlan,
    VisualizationStatus,
    VisualizationType,
)

_CODE = "from manim import *\nclass A(Scene):\n    pass\n"


def _msg():
    usage = SimpleNamespace(
        input_tokens=1, output_tokens=2, cache_read_input_tokens=0, cache_creation_input_tokens=0
    )
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=_CODE)],
        stop_reason="end_turn",
        stop_details=None,
        usage=usage,
    )


class _Stream:
    async def get_final_message(self):
        return _msg()


class _CM:
    async def __aenter__(self):
        return _Stream()

    async def __aexit__(self, *a):
        return False


class FakeClient:
    """Records every request in order: ('create', kw) = warm-up, ('stream', kw) = generation."""

    def __init__(self, create_exc=None):
        self.events = []
        self._create_exc = create_exc
        self.beta = SimpleNamespace(
            messages=SimpleNamespace(create=self._create, stream=self._stream)
        )

    async def _create(self, **kw):
        self.events.append(("create", kw))
        if self._create_exc:
            raise self._create_exc
        return _msg()

    def _stream(self, **kw):
        self.events.append(("stream", kw))
        return _CM()


def _plan(name):
    return VisualizationPlan(
        concept_name=name,
        visualization_type=VisualizationType.DATA_FLOW,
        duration_seconds=30,
        scenes=[Scene(order=1, description="d", duration_seconds=10, transitions="Write", elements=["Text"])],
        narration_points=["p"],
    )


def _cand(name, priority):
    return VisualizationCandidate(
        section_id="s1", concept_name=name, concept_description="d", context="c",
        visualization_type=VisualizationType.DATA_FLOW, priority=priority,
    )


class _Paper:
    class meta:
        title = "T"
    sections = []


def _setup(monkeypatch, provider, client, n):
    if provider == "anthropic":
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    else:
        monkeypatch.setenv("LLM_PROVIDER", "azure")
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "x")
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://e")
    monkeypatch.setattr(base, "_get_anthropic_client", lambda: client)

    async def fake_docs(topic, max_tokens, use_dedalus):
        return "LIVE DOCS for " + topic + " " * 100

    monkeypatch.setattr(mg, "get_manim_docs", fake_docs)
    for name in ("SectionAnalyzer", "VisualizationPlanner", "CodeValidator",
                 "SpatialValidator", "VoiceoverScriptValidator", "RenderTester"):
        monkeypatch.setattr(pipeline, name, lambda *a, **k: object())

    async def fake_analyze(analyzer, paper):
        names = ["Attention heads", "Positional encoding", "Beam search decoding"]
        return [_cand(names[i], 5 - i) for i in range(n)]

    async def fake_generate(candidate, generator, **kw):
        await generator.run(plan=_plan(candidate.concept_name))
        return Visualization(
            id="tmp", section_id="s1", concept=candidate.concept_name, storyboard="{}",
            manim_code=_CODE, video_url=None, status=VisualizationStatus.PENDING,
        )

    monkeypatch.setattr(pipeline, "_analyze_all_sections", fake_analyze)
    monkeypatch.setattr(pipeline, "generate_single_visualization", fake_generate)


async def test_warmup_once_before_generators_with_identical_system_block(monkeypatch):
    client = FakeClient()
    _setup(monkeypatch, "anthropic", client, n=3)
    out = await pipeline.generate_visualizations(_Paper())
    assert len(out) == 3

    kinds = [k for k, _ in client.events]
    assert kinds == ["create", "stream", "stream", "stream"]  # one warm-up, strictly first
    warm = client.events[0][1]
    assert warm["max_tokens"] == 0
    assert len(warm["system"]) == 1 and warm["system"][0]["cache_control"] == {"type": "ephemeral"}
    for banned in ("thinking", "temperature", "top_p", "top_k"):
        assert banned not in warm
    gen_systems = [kw["system"] for k, kw in client.events[1:]]
    for system in gen_systems:
        assert system[0] == warm["system"][0]  # byte-identical cached block
        assert "cache_control" not in system[1] and "LIVE DOCS" in system[1]["text"]
    assert gen_systems[0][1] != gen_systems[1][1]  # per-plan docs differ, prefix does not
    assert warm["model"] == client.events[1][1]["model"]


async def test_no_warmup_for_a_single_candidate(monkeypatch):
    client = FakeClient()
    _setup(monkeypatch, "anthropic", client, n=1)
    await pipeline.generate_visualizations(_Paper())
    assert [k for k, _ in client.events] == ["stream"]


async def test_warmup_is_noop_on_azure(monkeypatch):
    client = FakeClient()
    _setup(monkeypatch, "azure", client, n=2)

    async def fake_generate(candidate, generator, **kw):
        return Visualization(
            id="tmp", section_id="s1", concept=candidate.concept_name, storyboard="{}",
            manim_code=_CODE, video_url=None, status=VisualizationStatus.PENDING,
        )

    monkeypatch.setattr(pipeline, "generate_single_visualization", fake_generate)
    await pipeline.generate_visualizations(_Paper())
    assert client.events == []


async def test_warmup_failure_does_not_fail_generation(monkeypatch):
    client = FakeClient(create_exc=RuntimeError("rate limited"))
    _setup(monkeypatch, "anthropic", client, n=2)
    out = await pipeline.generate_visualizations(_Paper())
    assert len(out) == 2
    assert [k for k, _ in client.events] == ["create", "stream", "stream"]


async def test_visual_aids_also_warm_once(monkeypatch):
    client = FakeClient()
    _setup(monkeypatch, "anthropic", client, n=0)

    def aid(i):
        return VisualAidCandidate(
            section_id="s1", concept_name=f"Aid {i}", concept_description="d",
            visualization_type=VisualizationType.DATA_FLOW, paragraph_index=i,
            beats=["a", "b", "c"], context="c",
        )

    class _Analyzer:
        async def run(self, **kw):
            return [aid(1), aid(2)]

    monkeypatch.setattr(pipeline, "VisualAidAnalyzer", _Analyzer)
    paper = SimpleNamespace(
        meta=SimpleNamespace(title="T", abstract="a"),
        sections=[SimpleNamespace(id="s1", title="Intro", content="x" * 200)],
    )
    out = await pipeline.generate_visual_aids(paper)
    assert len(out) == 2
    assert [k for k, _ in client.events] == ["create", "stream", "stream"]
