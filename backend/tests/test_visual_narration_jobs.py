"""VISUAL_NARRATION reaches the planner through both job paths (mocks only, no network).

Both the legacy in-process worker and the Temporal generate activity call
agents.pipeline.generate_visualizations in their own process, and the planner
and generator read the env flag at call time, so setting it in the process
env is enough. These tests drive each job entry point with the REAL pipeline
and planner, stubbing only the LLM call, and check the planner prompt.
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import jobs.worker as worker
from agents import pipeline
from agents.visualization_planner import VisualizationPlanner
from db import queries
from db.models import Base, Paper, ProcessingJob, Section
from models.generation import VisualizationCandidate, VisualizationType
from temporal_app import activities
from temporal_app.activities import PipelineInput

PID = "2301.00001"
BLOCK_MARKER = "VISUAL AID:"


@pytest_asyncio.fixture
async def maker(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    m = async_sessionmaker(engine, expire_on_commit=False)
    async with m() as s:
        s.add(Paper(id=PID, title="T", authors=["A"], abstract="abs"))
        s.add(Section(id="s0", paper_id=PID, title="Intro", content="word " * 1600, order_index=0))
        s.add(ProcessingJob(id="job_1", status="queued", paper_id=PID))
        await s.commit()
    monkeypatch.setattr(worker, "async_session_maker", m)
    monkeypatch.setattr("db.connection.async_session_maker", m)
    monkeypatch.setattr(activities.activity, "info", lambda: SimpleNamespace(attempt=1))
    monkeypatch.setattr(activities.activity, "heartbeat", lambda *a, **k: None)

    async def _no_stale(db, arxiv_id):
        return False

    monkeypatch.setattr(queries, "paper_is_stale", _no_stale)
    # Don't stub asyncio.sleep: it's process-wide, and the activity's 30s
    # heartbeat loop would then spin forever without yielding.
    yield m
    await engine.dispose()


@pytest.fixture
def planner_prompts(monkeypatch):
    """Real generate_visualizations + real planner; the planner's LLM call is captured."""
    monkeypatch.setenv("LLM_PROVIDER", "azure")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "dummy-key-for-tests")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.delenv("VISUAL_NARRATION", raising=False)
    monkeypatch.setattr(pipeline, "ENABLE_VISUAL_AIDS", False)
    for name in ("SectionAnalyzer", "ManimGenerator", "CodeValidator", "SpatialValidator",
                 "VoiceoverScriptValidator", "RenderTester"):
        monkeypatch.setattr(pipeline, name, lambda *a, **k: object())

    async def fake_analyze(analyzer, paper):
        return [VisualizationCandidate(
            section_id="s0", concept_name="Attention heads", concept_description="d", context="c",
            visualization_type=VisualizationType.DATA_FLOW, priority=5,
        )]

    prompts: list[str] = []

    async def fake_llm_json(self, prompt, **kwargs):
        prompts.append(prompt)
        raise RuntimeError("stop after the planner prompt")  # viz is dropped; nothing else runs

    monkeypatch.setattr(pipeline, "_analyze_all_sections", fake_analyze)
    monkeypatch.setattr(VisualizationPlanner, "_call_llm_json", fake_llm_json)
    return prompts


async def _run_worker(maker):
    await worker._process_paper_job_impl("job_1", PID)


async def _run_activity(maker):
    await activities.generate_visualizations_for_paper(PipelineInput(job_id="job_1", arxiv_id=PID))


RUNNERS = [pytest.param(_run_worker, id="legacy-worker"), pytest.param(_run_activity, id="temporal-activity")]


@pytest.mark.parametrize("run", RUNNERS)
async def test_flag_on_planner_prompt_has_visual_aid_block(run, maker, planner_prompts, monkeypatch):
    monkeypatch.setenv("VISUAL_NARRATION", "1")
    await run(maker)
    assert len(planner_prompts) == 1
    assert BLOCK_MARKER in planner_prompts[0]


@pytest.mark.parametrize("run", RUNNERS)
async def test_flag_off_planner_prompt_has_no_visual_aid_block(run, maker, planner_prompts):
    await run(maker)
    assert len(planner_prompts) == 1
    assert BLOCK_MARKER not in planner_prompts[0]
