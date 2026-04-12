"""
asyncpg connection pool management.

A single pool is used for the lifetime of the process.  asyncpg handles
connection health, reconnects, and bounded concurrency automatically.

Usage
-----
    # In main.py lifespan:
    await db.init(settings.DATABASE_URL)
    ...
    await db.close()

    # In route handlers (FastAPI dependency):
    async def my_route(conn: asyncpg.Connection = Depends(db.get)):
        ...

    # Outside FastAPI (e.g. lifespan setup):
    async with db.acquire() as conn:
        await models.seed_personas(conn)
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Annotated, AsyncGenerator

import asyncpg
from fastapi import Depends

_pool: asyncpg.Pool | None = None

# ── lifecycle ──────────────────────────────────────────────────────────────────

async def init(database_url: str, password=None) -> None:
    """Create the connection pool and apply schema.

    Args:
        database_url: PostgreSQL connection string (password may be omitted).
        password: Optional static password string or callable (sync or async)
            that returns the password.  Pass ``settings.pg_password`` here when
            using Azure AD token authentication so each new connection fetches a
            fresh token.
    """
    global _pool
    kwargs: dict = {"min_size": 2, "max_size": 10, "ssl": "require"}
    if password is not None:
        kwargs["password"] = password
    _pool = await asyncpg.create_pool(database_url, **kwargs)
    async with _pool.acquire() as conn:
        from pathlib import Path
        schema = (Path(__file__).parent / "schema.sql").read_text()
        await conn.execute(schema)
        # Incremental migrations — idempotent ALTER TABLE statements for
        # columns added after the initial schema.  Safe to re-run.
        await conn.execute("""
            ALTER TABLE personas
            ADD COLUMN IF NOT EXISTS voice_instructions TEXT NOT NULL DEFAULT '';
        """)


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


# ── FastAPI dependency ─────────────────────────────────────────────────────────

async def get() -> AsyncGenerator[asyncpg.Connection, None]:
    """Yield a connection from the pool; intended as a FastAPI Depends() target."""
    assert _pool is not None, "Database not initialised — call db.init() first"
    async with _pool.acquire() as conn:
        yield conn


Conn = Annotated[asyncpg.Connection, Depends(get)]


# ── context manager for non-FastAPI use ───────────────────────────────────────

@asynccontextmanager
async def acquire():
    """Async context manager for acquiring a connection outside of FastAPI DI."""
    assert _pool is not None, "Database not initialised — call db.init() first"
    async with _pool.acquire() as conn:
        yield conn


# ── helpers ────────────────────────────────────────────────────────────────────

ANON_USER_ID = "00000000-0000-0000-0000-000000000001"


def new_id() -> str:
    return str(uuid.uuid4())
