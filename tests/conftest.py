"""
Shared pytest fixtures.

DB fixture:  connects to a PostgreSQL test database, applies the schema (once
             per session), then wraps each test in a rolled-back transaction.

App fixture: creates an HTTPX AsyncClient wired to the backend FastAPI app.

All async fixtures and tests share a single session-scoped event loop
(asyncio_default_fixture_loop_scope = session, asyncio_default_test_loop_scope =
session in pytest.ini), so asyncpg connections are never attached to a
different loop.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import psycopg2
import pytest
import pytest_asyncio

from backend.db import connection as db, models

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql://localhost/pdf_to_audio_test"
)

_SCHEMA_SQL = (Path(__file__).parent.parent / "backend" / "db" / "schema.sql").read_text()


# ── session-scoped schema setup (sync, runs once) ─────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def _apply_schema():
    """Drop and recreate public schema once per test session (sync via psycopg2)."""
    conn = psycopg2.connect(TEST_DATABASE_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        # Terminate any lingering connections so DROP SCHEMA doesn't block
        cur.execute("""
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = current_database() AND pid <> pg_backend_pid()
        """)
        cur.execute("DROP SCHEMA IF EXISTS public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute(_SCHEMA_SQL)
    conn.close()


# ── session-scoped pool ────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="session")
async def _test_db_pool(_apply_schema):
    """Create asyncpg pool once per session (reuses the session event loop)."""
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=5)
    yield pool
    await pool.close()


# ── per-test DB fixture ────────────────────────────────────────────────────────

class _SingleConnPool:
    """Minimal pool-like wrapper that always yields the same connection.
    Used to patch db._pool so that db.get() works inside tests.
    """

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn

    async def close(self) -> None:
        pass


@pytest_asyncio.fixture
async def mem_db(_test_db_pool) -> AsyncGenerator[asyncpg.Connection, None]:
    """
    Fresh transactional scope per test.

    Acquires a connection from the session pool, wraps the entire test in a
    transaction that is rolled back at the end.
    Patches db._pool so all helpers using db.get() see this connection.
    """
    async with _test_db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()

        # Seed within this transaction
        await conn.execute(
            "INSERT INTO users (id, display_name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            db.ANON_USER_ID, "Anonymous",
        )
        await models.seed_personas(conn)

        original_pool = db._pool
        db._pool = _SingleConnPool(conn)

        yield conn

        db._pool = original_pool
        await tr.rollback()


# ── FastAPI test client ─────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client(mem_db) -> AsyncGenerator:
    """Async HTTPX client wired to the backend FastAPI app."""
    from httpx import AsyncClient, ASGITransport
    from backend.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
