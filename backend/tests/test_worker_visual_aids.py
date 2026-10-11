"""Legacy in-process job path with visual aids (mocked pipeline + render, SQLite file, no network)."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import jobs.worker as worker
from agents import pipeline
from db import queries
from db.models import Base, Paper, ProcessingJob, Section
from models.generation import Visualization as GenViz

PID = "2301.00001"
SUFFIX = "2301_00001"


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

    async def _no_stale(db, arxiv_id):
        return False

    monkeypatch.setattr(queries, "paper_is_stale", _no_stale)

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(worker.asyncio, "sleep", _no_sleep)
    yield m
    await engine.dispose()


def _narrated(n=2):
    return [GenViz(id=f"g{i}", section_id="s0", concept=f"N{i}", storyboard="sb", manim_code=f"code-n{i}")
            for i in range(n)]


def _aids(n=2):
    return [GenViz(id=f"a{i}", section_id="s0", concept=f"A{i}", storyboard="sb", manim_code=f"code-a{i}",
                   kind="visual_aid", anchor_paragraph=i + 1) for i in range(n)]


class Harness:
    def __init__(self, monkeypatch, narrated, aids=None, aid_error=None, fail_codes=()):
        self.aid_calls = []
        self.renders = []

        async def fake_generate(paper):
            return narrated

        async def fake_aids(paper, **kw):
            self.aid_calls.append(kw)
            if aid_error:
                raise aid_error
            return aids or []

        async def fake_render(viz_id, manim_code, quality="low_quality", kind="narrated", **_):
            self.renders.append((viz_id, kind))
            if manim_code in fail_codes:
                raise RuntimeError("boom")
            return f"https://x/{viz_id}.mp4"

        monkeypatch.setattr(worker, "generate_visualizations", fake_generate)
        monkeypatch.setattr(pipeline, "generate_visual_aids", fake_aids)
        monkeypatch.setattr(worker, "process_visualization", fake_render)


async def _run(maker):
    await worker._process_paper_job_impl("job_1", PID)
    async with maker() as db:
        job = await queries.get_job(db, "job_1")
        rows = {v.id: v for v in await queries.get_visualizations_for_paper(db, PID, include_superseded=True)}
    return job, rows


async def test_flag_off_does_not_call_aids_and_rows_are_as_before(maker, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_VISUAL_AIDS", False)
    h = Harness(monkeypatch, _narrated(2), aids=_aids(2))
    job, rows = await _run(maker)
    assert h.aid_calls == []
    assert sorted(rows) == [f"viz_{SUFFIX}_1", f"viz_{SUFFIX}_2"]
    assert job.status == "completed" and job.sections_total == 2 and job.sections_completed == 2
    assert {k for _, k in h.renders} == {"narrated"}


async def test_flag_on_persists_and_renders_aids(maker, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_VISUAL_AIDS", True)
    h = Harness(monkeypatch, _narrated(2), aids=_aids(2))
    job, rows = await _run(maker)
    assert h.aid_calls[0]["skip_concepts"] == ["N0", "N1"]
    assert sorted(rows) == sorted([f"viz_{SUFFIX}_1", f"viz_{SUFFIX}_2", f"viz_{SUFFIX}_101", f"viz_{SUFFIX}_102"])
    assert rows[f"viz_{SUFFIX}_1"].kind == "narrated" and rows[f"viz_{SUFFIX}_1"].anchor_paragraph is None
    aid = rows[f"viz_{SUFFIX}_102"]
    assert aid.kind == "visual_aid" and aid.anchor_paragraph == 2 and aid.status == "complete"
    assert aid.concept == "A1"
    assert (f"viz_{SUFFIX}_101", "visual_aid") in h.renders
    assert (f"viz_{SUFFIX}_1", "narrated") in h.renders
    assert job.status == "completed" and job.sections_total == 4 and job.sections_completed == 4


async def test_aid_generation_error_does_not_fail_job(maker, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_VISUAL_AIDS", True)
    Harness(monkeypatch, _narrated(2), aid_error=RuntimeError("llm down"))
    job, rows = await _run(maker)
    assert job.status == "completed" and job.sections_total == 2
    assert sorted(rows) == [f"viz_{SUFFIX}_1", f"viz_{SUFFIX}_2"]


async def test_aid_render_failure_does_not_fail_job(maker, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_VISUAL_AIDS", True)
    Harness(monkeypatch, _narrated(1), aids=_aids(2), fail_codes=("code-a0", "code-a1"))
    job, rows = await _run(maker)
    assert job.status == "completed" and job.current_step == "Complete"
    assert job.sections_total == 3 and job.sections_completed == 1
    assert rows[f"viz_{SUFFIX}_101"].status == "failed"


async def test_all_narrated_failing_still_fails_job_even_if_aids_render(maker, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_VISUAL_AIDS", True)
    Harness(monkeypatch, _narrated(1), aids=_aids(1), fail_codes=("code-n0",))
    job, _ = await _run(maker)
    assert job.status == "failed"


async def test_no_narrated_skips_aid_generation(maker, monkeypatch):
    monkeypatch.setattr(pipeline, "ENABLE_VISUAL_AIDS", True)
    h = Harness(monkeypatch, [], aids=_aids(1))
    job, _ = await _run(maker)
    assert h.aid_calls == [] and job.status == "failed"


@pytest.mark.parametrize("n", [5])
async def test_aid_ids_disjoint_from_max_narrated(maker, monkeypatch, n):
    monkeypatch.setattr(pipeline, "ENABLE_VISUAL_AIDS", True)
    Harness(monkeypatch, _narrated(n), aids=_aids(3))
    _, rows = await _run(maker)
    assert len(rows) == n + 3
