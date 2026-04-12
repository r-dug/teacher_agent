"""
Shared pytest fixtures.

DB fixture:  connects to a PostgreSQL test database, applies the schema (once
             per session), then wraps each test in a rolled-back transaction.

App fixture: creates an HTTPX AsyncClient wired to the backend FastAPI app.

All async fixtures and tests share a single session-scoped event loop
(asyncio_default_fixture_loop_scope = session, asyncio_default_test_loop_scope =
session in pytest.ini), so asyncpg connections are never attached to a
different loop.

Connection strategy:
  - If TEST_DATABASE_URL is set, use it directly.
  - Otherwise, build a connection from PG* env vars (same ones the app uses).
  - If the host is an Azure PG (.postgres.database.azure.com), fetch an AAD
    token for authentication.
"""

from __future__ import annotations

import os
import urllib.parse
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import asyncpg
import psycopg2
import pytest
import pytest_asyncio

from backend.db import connection as db, models

_SCHEMA_SQL = (Path(__file__).parent.parent / "backend" / "db" / "schema.sql").read_text()


# ── connection helpers ────────────────────────────────────────────────────────

def _resolve_connection_params() -> tuple[str, dict]:
    """Return (dsn, extra_kwargs) for both psycopg2 and asyncpg.

    Extra kwargs may include ``password`` and ``ssl``.
    """
    explicit = os.getenv("TEST_DATABASE_URL")
    if explicit:
        return explicit, {}

    host = os.getenv("PGHOST", "localhost")
    port = os.getenv("PGPORT", "5432")
    user = os.getenv("PGUSER", "")
    dbname = os.getenv("PGDATABASE", "postgres")

    if user:
        dsn = f"postgresql://{urllib.parse.quote(user, safe='')}@{host}:{port}/{dbname}"
    else:
        dsn = f"postgresql://{host}:{port}/{dbname}"

    kwargs: dict = {}
    if ".postgres.database.azure.com" in host:
        from azure.identity import DefaultAzureCredential
        cred = DefaultAzureCredential()
        token = cred.get_token("https://ossrdbms-aad.database.windows.net/.default").token
        kwargs["password"] = token
        kwargs["ssl"] = "require"

    return dsn, kwargs


# ── session-scoped schema setup (sync, runs once) ─────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def _apply_schema():
    """Apply the schema idempotently (CREATE TABLE IF NOT EXISTS) via psycopg2."""
    dsn, kwargs = _resolve_connection_params()
    connect_kwargs: dict = {}
    if "password" in kwargs:
        connect_kwargs["password"] = kwargs["password"]
    if "ssl" in kwargs:
        connect_kwargs["sslmode"] = "require"

    conn = psycopg2.connect(dsn, **connect_kwargs)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(_SCHEMA_SQL)
        # Incremental migrations — same list as backend/db/connection.py
        cur.execute("""
            ALTER TABLE personas
            ADD COLUMN IF NOT EXISTS voice_instructions TEXT NOT NULL DEFAULT '';
        """)
    conn.close()


# ── session-scoped pool ────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="session")
async def _test_db_pool(_apply_schema):
    """Create asyncpg pool once per session (reuses the session event loop)."""
    dsn, kwargs = _resolve_connection_params()
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5, **kwargs)
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
    transaction that is rolled back at the end — no data persists between tests.
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
