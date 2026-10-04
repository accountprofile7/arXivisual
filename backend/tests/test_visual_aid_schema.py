"""Visual-aid schema: kind/anchor_paragraph columns are added to pre-existing
databases by init_db (no alembic), and VisualAidCandidate validates leniently
(temp SQLite file, no network)."""

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db import connection
from models.generation import VisualAidCandidate, Visualization

OLD_DDL = """
CREATE TABLE visualizations (
    id VARCHAR NOT NULL PRIMARY KEY,
    paper_id VARCHAR NOT NULL,
    section_id VARCHAR,
    concept VARCHAR NOT NULL,
    storyboard JSON,
    manim_code TEXT,
    video_url VARCHAR,
    status VARCHAR,
    error TEXT,
    created_at DATETIME
)
"""


@pytest.fixture
async def old_db(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    async with engine.begin() as conn:
        await conn.exec_driver_sql(OLD_DDL)
        await conn.exec_driver_sql(
            "INSERT INTO visualizations (id, paper_id, concept, status) VALUES ('viz_old_1', 'p1', 'Old', 'complete')"
        )
    monkeypatch.setattr(connection, "engine", engine)
    monkeypatch.setattr(connection, "async_session_maker", async_sessionmaker(engine, expire_on_commit=False))
    yield engine
    await engine.dispose()


async def _columns(engine):
    async with engine.connect() as conn:
        rows = (await conn.execute(text("PRAGMA table_info(visualizations)"))).all()
    return {r[1] for r in rows}


async def test_init_db_adds_missing_columns_and_old_rows_read_null(old_db):
    assert "kind" not in await _columns(old_db)
    await connection.init_db()
    cols = await _columns(old_db)
    assert {"kind", "anchor_paragraph"} <= cols
    async with old_db.connect() as conn:
        row = (await conn.execute(text("SELECT kind, anchor_paragraph FROM visualizations WHERE id='viz_old_1'"))).one()
    assert row == (None, None)


async def test_init_db_twice_is_noop(old_db):
    await connection.init_db()
    await connection.init_db()
    assert {"kind", "anchor_paragraph"} <= await _columns(old_db)


def test_visualization_defaults():
    v = Visualization(id="v", section_id="s", concept="c", storyboard="{}", manim_code="x")
    assert v.kind == "narrated"
    assert v.anchor_paragraph is None


def _candidate(**kw):
    base = dict(
        section_id="s1",
        concept_name="Attention",
        concept_description="desc",
        visualization_type="architecture",
        paragraph_index=2,
        beats=["a", "b"],
    )
    base.update(kw)
    return VisualAidCandidate(**base)


def test_visual_aid_candidate_valid():
    c = _candidate()
    assert c.context == ""
    assert c.paragraph_index == 2


def test_visual_aid_candidate_truncates_beats_beyond_four():
    assert _candidate(beats=list("abcdefg")).beats == list("abcd")


def test_visual_aid_candidate_rejects_negative_paragraph():
    with pytest.raises(ValidationError):
        _candidate(paragraph_index=-1)


def test_visual_aid_candidate_rejects_bad_type():
    with pytest.raises(ValidationError):
        _candidate(visualization_type="nope")
