"""
aiosqlite connection management.

A single connection is used for the lifetime of the process.  SQLite in WAL
mode allows concurrent readers; writes are serialised by aiosqlite's internal
thread.  This is intentional for the prototype — see design doc.

Usage
-----
    # In main.py lifespan:
    await db.init(settings.DB_PATH)
    ...
    await db.close()

    # In route handlers (FastAPI dependency):
    async def my_route(conn: aiosqlite.Connection = Depends(db.get)):
        ...
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite

_conn: aiosqlite.Connection | None = None
_SCHEMA_FILE = Path(__file__).parent / "schema.sql"

# ── lifecycle ──────────────────────────────────────────────────────────────────

async def init(db_path: Path | str) -> None:
    """Open the database, apply schema, run migrations, and seed defaults."""
    global _conn
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    _conn = await aiosqlite.connect(str(db_path))
    _conn.row_factory = aiosqlite.Row
    await _conn.executescript(_SCHEMA_FILE.read_text())
    await _conn.commit()
    await _run_migrations(_conn)
    await _seed_default_user(_conn)


async def close() -> None:
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


# ── FastAPI dependency ─────────────────────────────────────────────────────────

async def get() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Yield the shared connection; intended as a FastAPI Depends() target."""
    assert _conn is not None, "Database not initialised — call db.init() first"
    yield _conn


# ── helpers ────────────────────────────────────────────────────────────────────

async def _run_migrations(conn: aiosqlite.Connection) -> None:
    """Apply additive schema changes that CREATE TABLE IF NOT EXISTS can't handle."""
    # Add is_admin column to users if it doesn't exist yet (upgrading old DBs).
    async with conn.execute("PRAGMA table_info(users)") as cur:
        cols = {row[1] async for row in cur}
    if "is_admin" not in cols:
        await conn.execute(
            "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"
        )
        await conn.commit()

    # Add course_id and description to lessons (courses feature).
    async with conn.execute("PRAGMA table_info(lessons)") as cur:
        lesson_cols = {row[1] async for row in cur}
    if "course_id" not in lesson_cols:
        await conn.execute(
            "ALTER TABLE lessons ADD COLUMN course_id TEXT REFERENCES courses(id) ON DELETE SET NULL"
        )
    if "description" not in lesson_cols:
        await conn.execute(
            "ALTER TABLE lessons ADD COLUMN description TEXT"
        )
    if "lesson_goal" not in lesson_cols:
        await conn.execute(
            "ALTER TABLE lessons ADD COLUMN lesson_goal TEXT"
        )
    await conn.commit()


async def _seed_default_user(conn: aiosqlite.Connection) -> None:
    """Ensure the single prototype user exists."""
    ANON_ID = "00000000-0000-0000-0000-000000000001"
    await conn.execute(
        "INSERT OR IGNORE INTO users (id, display_name) VALUES (?, ?)",
        (ANON_ID, "Anonymous"),
    )
    await conn.commit()


ANON_USER_ID = "00000000-0000-0000-0000-000000000001"


def new_id() -> str:
    return str(uuid.uuid4())
