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
    """Open the database, apply schema, and seed the default anonymous user."""
    global _conn
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    _conn = await aiosqlite.connect(str(db_path))
    _conn.row_factory = aiosqlite.Row
    await _conn.executescript(_SCHEMA_FILE.read_text())
    await _conn.commit()
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
