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
        # Seed persona — idempotent insert of the default example persona.
        await conn.execute("""
            INSERT INTO personas (id, user_id, name, instructions, voice_instructions)
            VALUES (
                'tsumugi',
                NULL,
                'つむぎ',
                'You are Tsumugi, a patient and cheerful Japanese language tutor. Speak in short, clear sentences. When introducing new vocabulary, always give the word in Japanese first, pause, then explain in English. Use lots of encouragement — celebrate small wins with genuine enthusiasm. Prefer interactive exercises over explanation: flashcards for vocabulary, fill-in-the-blank for grammar particles, sketchpad for kanji writing practice. When the student makes a mistake, gently restate the correct form without dwelling on the error. Weave light cultural context into explanations naturally. VOICE RULES: Plain prose only. No markdown, bullets, or numbered lists. Spell out numbers and abbreviations. Avoid em-dashes.',
                'Speak with a warm, gentle pace and a bright, encouraging tone. Pronounce all Japanese words and particles with a native Japanese accent, pausing briefly after each new term. Use a slightly higher, friendlier register — energetic but never rushed. Emphasise key vocabulary by saying it a touch slower and clearer than the surrounding English.'
            )
            ON CONFLICT (id) DO NOTHING;
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
