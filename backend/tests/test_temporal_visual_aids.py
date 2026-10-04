"""Temporal-path visual aids: activities with mocked pipeline + SQLite (hermetic, no Temporal server)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agents import pipeline
from db import queries
from db.models import Base, Paper, ProcessingJob, Section
from models.generation import Visualization as GenViz
from temporal_app import activities
from temporal_app.activities import PipelineInput, RenderInput

PID = "2301.00001"
SUFFIX = "2301_00001"
PARAMS = PipelineInput(job_id="job_1", arxiv_id=PID)


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
    monkeypatch.setattr("db.connection.async_session_maker", m)
    monkeypatch.setattr(activities.activity, "info", lambda: SimpleNamespace(attempt=1))
    beats: list[str] = []
    monkeypatch.setattr(activities.activity, "heartbeat", lambda *a, **k: beats.append(a[0] if a else ""))
    m.beats = beats
    yield m
    await engine.dispose()


async def _seed(m, viz_id, concept, kind=None, anchor=None, status="pending"):
    async with m() as db:
        await queries.create_visualization(
            db, viz_id=viz_id, paper_id=PID, section_id="s0", concept=concept,
            status=status, manim_code=f"code-{concept}", kind=kind, anchor_paragraph=anchor,
        )


async def _rows(m):
    async with m() as db:
        return await queries.get_visualizations_for_paper(db, PID, include_superseded=True)


def _gv(concept, kind="narrated", anchor=None):
    return GenViz(id=f"g-{concept}", section_id="s0", concept=concept, storyboard="sb",
                  manim_code=f"code-{concept}", kind=kind, anchor_paragraph=anchor)


def _patch_narrated(monkeypatch, produce=()):
    seen: dict = {}

    async def fake(paper, *, max_visualizations, on_visualization, skip_concepts):
        seen.update(max=max_visualizations, skip=set(skip_concepts))
        for v in produce:
            await on_visualization(v)

    monkeypatch.setattr(pipeline, "generate_visualizations", fake)
    return seen


def _patch_aids(monkeypatch, produce=(), raise_after=None, enabled=True):
    seen: dict = {"calls": 0}
    monkeypatch.setattr(pipeline, "ENABLE_VISUAL_AIDS", enabled)

    async def fake(paper, *, skip_concepts=(), max_total, on_visualization=None, **kw):
        seen.update(calls=seen["calls"] + 1, max=max_total, skip=set(skip_concepts))
        for v in produce:
            await on_visualization(v)
        if raise_after is not None:
            raise raise_after

    monkeypatch.setattr(pipeline, "generate_visual_aids", fake)
    return seen


# --- dataclass / queries -----------------------------------------------------

def test_render_input_kind_defaults_to_narrated():
    assert RenderInput(job_id="j", viz_id="v", manim_code="c").kind == "narrated"


@pytest.mark.asyncio
async def test_insert_with_next_index_passes_kind_and_anchor(maker):
    vid = await queries.insert_visualization_with_next_index(
        paper_suffix=SUFFIX, paper_id=PID, section_id="s0", concept="c", storyboard=None,
        manim_code="x", session_maker=maker, kind="visual_aid", anchor_paragraph=3,
    )
    plain = await queries.insert_visualization_with_next_index(
        paper_suffix=SUFFIX, paper_id=PID, section_id="s0", concept="d", storyboard=None,
        manim_code="x", session_maker=maker,
    )
    rows = {r.id: r for r in await _rows(maker)}
    assert (rows[vid].kind, rows[vid].anchor_paragraph) == ("visual_aid", 3)
    assert (rows[plain].kind, rows[plain].anchor_paragraph) == (None, None)
    assert vid == f"viz_{SUFFIX}_1" and plain == f"viz_{SUFFIX}_2"


# --- narrated activity: counts narrated rows only ------------------------------

@pytest.mark.asyncio
async def test_narrated_checkpoints_are_kind_narrated(maker, monkeypatch):
    _patch_narrated(monkeypatch, produce=[_gv("N1")])
    out = await activities.generate_visualizations_for_paper(PARAMS)
    assert [(r.kind) for r in out] == ["narrated"]
    assert [r.kind for r in await _rows(maker)] == ["narrated"]


@pytest.mark.asyncio
async def test_narrated_resume_ignores_aid_rows(maker, monkeypatch):
    await _seed(maker, f"viz_{SUFFIX}_1", "N1", kind="narrated")
    await _seed(maker, f"viz_{SUFFIX}_2", "N2")  # legacy NULL kind = narrated
    for i in range(3, 8):
        await _seed(maker, f"viz_{SUFFIX}_{i}", f"A{i}", kind="visual_aid", anchor=1)
    seen = _patch_narrated(monkeypatch)
    out = await activities.generate_visualizations_for_paper(PARAMS)
    assert seen["max"] == pipeline.MAX_VISUALIZATIONS - 2
    assert seen["skip"] == {"N1", "N2"}
    assert sorted(r.viz_id for r in out) == [f"viz_{SUFFIX}_1", f"viz_{SUFFIX}_2"]
    assert all(r.kind == "narrated" for r in out)


# --- aid activity ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_aids_flag_off_returns_empty_without_generating(maker, monkeypatch):
    await _seed(maker, f"viz_{SUFFIX}_1", "N1", kind="narrated")
    seen = _patch_aids(monkeypatch, enabled=False)
    assert await activities.generate_visual_aids_for_paper(PARAMS) == []
    assert seen["calls"] == 0


@pytest.mark.asyncio
async def test_aids_checkpoint_kind_anchor_and_return_aid_inputs(maker, monkeypatch):
    await _seed(maker, f"viz_{SUFFIX}_1", "N1", kind="narrated")
    seen = _patch_aids(monkeypatch, produce=[_gv("A1", "visual_aid", 2), _gv("A2", "visual_aid", 0)])
    out = await activities.generate_visual_aids_for_paper(PARAMS)
    assert [r.kind for r in out] == ["visual_aid", "visual_aid"]
    assert seen["max"] == pipeline.MAX_VISUAL_AIDS
    assert seen["skip"] == {"N1"}
    rows = {r.id: r for r in await _rows(maker)}
    for ri, anchor in zip(out, (2, 0), strict=True):
        assert rows[ri.viz_id].kind == "visual_aid"
        assert rows[ri.viz_id].anchor_paragraph == anchor
        assert rows[ri.viz_id].status == "pending"
    assert maker.beats  # heartbeat per checkpoint
    async with maker() as db:
        job = await queries.get_job(db, "job_1")
    assert job.sections_total == 3  # 1 narrated + 2 aids: progress counts every render


@pytest.mark.asyncio
async def test_aid_resume_counts_only_aids_and_skips_known_concepts(maker, monkeypatch):
    for i in range(1, 6):  # a full set of narrated rows must not consume aid slots
        await _seed(maker, f"viz_{SUFFIX}_{i}", f"N{i}", kind="narrated")
    await _seed(maker, f"viz_{SUFFIX}_6", "A1", kind="visual_aid", anchor=1)
    await _seed(maker, f"viz_{SUFFIX}_7", "A2", kind="visual_aid", anchor=2)
    seen = _patch_aids(monkeypatch)
    out = await activities.generate_visual_aids_for_paper(PARAMS)
    assert seen["max"] == pipeline.MAX_VISUAL_AIDS - 2
    assert seen["skip"] == {"N1", "N2", "N3", "N4", "N5", "A1", "A2"}
    assert sorted(r.viz_id for r in out) == [f"viz_{SUFFIX}_6", f"viz_{SUFFIX}_7"]
    assert all(r.kind == "visual_aid" for r in out)


@pytest.mark.asyncio
async def test_aid_slots_full_skips_generation(maker, monkeypatch):
    await _seed(maker, f"viz_{SUFFIX}_1", "N1", kind="narrated")
    for i in range(pipeline.MAX_VISUAL_AIDS):
        await _seed(maker, f"viz_{SUFFIX}_{i + 2}", f"A{i}", kind="visual_aid", anchor=0)
    seen = _patch_aids(monkeypatch)
    out = await activities.generate_visual_aids_for_paper(PARAMS)
    assert seen["calls"] == 0
    assert len(out) == pipeline.MAX_VISUAL_AIDS


@pytest.mark.asyncio
async def test_aid_exception_returns_partial_checkpointed_list(maker, monkeypatch):
    await _seed(maker, f"viz_{SUFFIX}_1", "N1", kind="narrated")
    _patch_aids(monkeypatch, produce=[_gv("A1", "visual_aid", 1)], raise_after=RuntimeError("llm down"))
    out = await activities.generate_visual_aids_for_paper(PARAMS)
    assert len(out) == 1 and out[0].kind == "visual_aid"


@pytest.mark.asyncio
async def test_aid_exception_with_nothing_checkpointed_returns_empty(maker, monkeypatch):
    await _seed(maker, f"viz_{SUFFIX}_1", "N1", kind="narrated")
    _patch_aids(monkeypatch, raise_after=RuntimeError("llm down"))
    assert await activities.generate_visual_aids_for_paper(PARAMS) == []


# --- render_visualization -----------------------------------------------------

def _patch_render(monkeypatch, verdict_severity="major"):
    import rendering

    calls: list[dict] = []

    async def fake(**kw):
        calls.append(kw)
        if kw.get("collect_qa"):
            return "http://v", SimpleNamespace(severity=verdict_severity, issues=["x"])
        return "http://v"

    monkeypatch.setattr(rendering, "process_visualization", fake)
    monkeypatch.setenv("ENABLE_VISUAL_QA", "1")
    monkeypatch.setenv("VISUAL_QA_REPAIR", "1")
    return calls


@pytest.mark.asyncio
async def test_render_passes_kind_and_never_recommends_repair_for_aids(maker, monkeypatch):
    await _seed(maker, f"viz_{SUFFIX}_1", "A1", kind="visual_aid", anchor=1)
    calls = _patch_render(monkeypatch)
    aid = await activities.render_visualization(
        RenderInput(job_id="job_1", viz_id=f"viz_{SUFFIX}_1", manim_code="c", kind="visual_aid"))
    assert calls[-1]["kind"] == "visual_aid"
    assert aid.succeeded and aid.severity == "major" and aid.repair_recommended is False

    narrated = await activities.render_visualization(
        RenderInput(job_id="job_1", viz_id=f"viz_{SUFFIX}_1", manim_code="c"))
    assert calls[-1]["kind"] == "narrated"
    assert narrated.repair_recommended is True


# --- finalize: aid rows follow the same supersede / fail-pending rules ---------

@pytest.mark.asyncio
async def test_finalize_supersedes_old_aids_and_fails_pending_aids(maker, monkeypatch):
    import analytics

    monkeypatch.setattr(analytics, "job_outcome", lambda **kw: None)
    from datetime import datetime, timedelta

    async with maker() as db:
        job = await queries.get_job(db, "job_1")
        job.paper_id = PID
        await db.commit()
        old = job.created_at - timedelta(hours=1)
    await _seed(maker, f"viz_{SUFFIX}_1", "OLDN", kind="narrated", status="complete")
    await _seed(maker, f"viz_{SUFFIX}_2", "OLDA", kind="visual_aid", anchor=1, status="complete")
    async with maker() as db:
        for r in await queries.get_visualizations_for_paper(db, PID):
            r.created_at = old
        await db.commit()
    await _seed(maker, f"viz_{SUFFIX}_3", "NEWN", kind="narrated", status="complete")
    await _seed(maker, f"viz_{SUFFIX}_4", "NEWA", kind="visual_aid", anchor=1, status="pending")
    assert datetime.utcnow()  # naive UTC, as the rest of the codebase
    await activities.finalize_job(activities.ProgressUpdate(job_id="job_1", completed=1, total=1))
    status = {r.id: r.status for r in await _rows(maker)}
    assert status == {
        f"viz_{SUFFIX}_1": "superseded",
        f"viz_{SUFFIX}_2": "superseded",
        f"viz_{SUFFIX}_3": "complete",
        f"viz_{SUFFIX}_4": "failed",
    }


@pytest.mark.asyncio
async def test_aid_resume_reapplies_per_section_cap(maker, monkeypatch):
    await _seed(maker, f"viz_{SUFFIX}_1", "N1", kind="narrated")
    await _seed(maker, f"viz_{SUFFIX}_2", "A1", kind="visual_aid", anchor=1)
    await _seed(maker, f"viz_{SUFFIX}_3", "A2", kind="visual_aid", anchor=2)
    monkeypatch.setattr(pipeline, "VISUAL_AIDS_PER_SECTION", 3)
    # s0 already holds 2 aids: exactly one more fits, the next is dropped.
    _patch_aids(monkeypatch, produce=[_gv("A3", "visual_aid", 0), _gv("A4", "visual_aid", 0)])
    out = await activities.generate_visual_aids_for_paper(PARAMS)
    assert len(out) == 3
    rows = [r for r in await _rows(maker) if r.kind == "visual_aid"]
    assert sorted(r.concept for r in rows) == ["A1", "A2", "A3"]


@pytest.mark.asyncio
async def test_render_progress_never_goes_backwards(maker):
    async with maker() as db:
        await queries.update_job_status(db, "job_1", progress=0.9)
    # Total grew (aids joined) after a high-water mark: the bar must hold.
    await activities.update_render_progress(activities.ProgressUpdate(job_id="job_1", completed=1, total=10))
    async with maker() as db:
        job = await queries.get_job(db, "job_1")
    assert job.progress == 0.9 and job.sections_completed == 1
    await activities.update_render_progress(activities.ProgressUpdate(job_id="job_1", completed=10, total=10))
    async with maker() as db:
        job = await queries.get_job(db, "job_1")
    assert job.progress == pytest.approx(0.95)
