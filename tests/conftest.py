"""
Shared pytest fixtures.

DB fixture:  spins up an in-memory SQLite database, applies the schema, and
             seeds the default anonymous user.  Each test gets a fresh DB.

App fixture: creates a FastAPI TestClient wired to the in-memory DB.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import aiosqlite
import pytest
import pytest_asyncio

from backend.db import connection as db, models


# ── event loop ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    """Use a single event loop for the whole test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ── in-memory database ─────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def mem_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """
    Fresh in-memory SQLite DB per test.

    Patches db._conn so that all helpers using db.get() see this connection.
    """
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    from pathlib import Path
    schema = (Path(__file__).parent.parent / "backend" / "db" / "schema.sql").read_text()
    await conn.executescript(schema)
    await conn.commit()

    # Seed anonymous user
    await conn.execute(
        "INSERT OR IGNORE INTO users (id, display_name) VALUES (?, ?)",
        (db.ANON_USER_ID, "Anonymous"),
    )
    await conn.commit()
    await models.seed_personas(conn)

    # Patch the global connection so db.get() yields this conn
    original = db._conn
    db._conn = conn
    yield conn
    db._conn = original
    await conn.close()


# ── FastAPI test client ─────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client(mem_db):
    """Async HTTPX client wired to the backend FastAPI app."""
    from httpx import AsyncClient, ASGITransport
    from backend.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
