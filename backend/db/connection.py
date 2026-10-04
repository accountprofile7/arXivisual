"""
Database connection management for ArXiviz.

Uses SQLite with aiosqlite for local development.
Automatically switches to PostgreSQL when DATABASE_URL environment variable is set (Railway/Render).
"""

import logging
import os

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Base

# Use DATABASE_URL from environment (Railway/Render) or fallback to SQLite
DATABASE_URL = os.getenv("DATABASE_URL")

# Railway/Render provide postgres:// URLs, but we need postgresql+asyncpg://
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
else:
    # Local development fallback
    DATABASE_URL = "sqlite+aiosqlite:///./arxiviz.db"

# Create async engine
engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv("ENVIRONMENT", "development") == "development",  # Log SQL in dev
    # Additional pool settings for PostgreSQL (ignored for SQLite)
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

# Session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    """
    FastAPI dependency that provides a database session.

    Usage in routes:
        @router.get("/example")
        async def example(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


# Columns added after the first release. There is no alembic and create_all never
# alters an existing table, so init_db adds any that are missing: (table, column,
# DDL type). Types must be valid on both SQLite and Postgres. New columns must be
# nullable (or carry a DEFAULT) so existing rows stay valid.
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("visualizations", "kind", "VARCHAR"),
    ("visualizations", "anchor_paragraph", "INTEGER"),
]


def _add_missing_columns(sync_conn) -> None:
    """Idempotently ALTER TABLE ... ADD COLUMN for each missing _ADDED_COLUMNS entry."""
    insp = inspect(sync_conn)
    # Several replicas (API + worker) run init_db concurrently on Postgres; IF NOT
    # EXISTS makes a lost race a no-op instead of a startup crash. SQLite lacks it.
    if_not_exists = "IF NOT EXISTS " if sync_conn.dialect.name == "postgresql" else ""
    existing: dict[str, set[str]] = {}
    for table, column, ddl_type in _ADDED_COLUMNS:
        if table not in existing:
            existing[table] = {c["name"] for c in insp.get_columns(table)}
        if column not in existing[table]:
            sync_conn.execute(
                text(f"ALTER TABLE {table} ADD COLUMN {if_not_exists}{column} {ddl_type}")
            )
            existing[table].add(column)


async def init_db():
    """
    Initialize database tables.

    Call this on application startup.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
    # Idempotent data hygiene, run at every API start (one cheap scan of the
    # visualizations table): hide pre-fix truncated-id video rows where a
    # full-id row exists (see queries.supersede_legacy_truncated_rows).
    from db import queries

    async with async_session_maker() as db:
        retired = await queries.supersede_legacy_truncated_rows(db)
        if retired:
            logging.getLogger(__name__).info("Superseded %d legacy truncated-id visualization row(s)", retired)
