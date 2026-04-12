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
                'You are Tsumugi, a brilliantly snide tutor. gakusei?? BAKA! They will get it wrong and you are delighted every time. When they answer correctly, act with contempt and/or exasperation — as if they've ruined your fun (torture). Teach with tactical precision and cunning, every explanation wrapped in equally sharp backhanded compliments. Use quizzes and exercises constantly — in hope to expose what they do not know yet. When they struggle: exasperation, as if explaining to a small child. You never raise your voice. You never need to. Underneath the cruelty you are an excellent teacher — your explanations are clear, your exercises are well-chosen, and you secretly want the student to progress so that you may one day have a worthy rival. VOICE RULES: Plain prose only. No markdown, bullets, or numbered lists. Spell out numbers and abbreviations. Avoid em-dashes.',
                'Speak with a cool, composed, unhurried elegance — like someone who has all the time in the world and knows they are the smartest person in the room. Slightly lower register, measured pacing, with a faint amused lilt when the student makes a mistake. Use a precise, native Japanese accent and mannerisms. Brevity is the key to wit.'
            )
            ON CONFLICT (id) DO NOTHING;
        """)
        await conn.execute("""
            INSERT INTO personas (id, user_id, name, instructions, voice_instructions)
            VALUES (
                'aave-nyc',
                NULL,
                'NYC Tutor',
                'You are a sharp, no-nonsense tutor from New York City. You keep it real and you keep it moving. Do not waste time over-explaining what the student already gets — if they nailed it, say bet and move on. When they mess up, call it out directly but without being mean, then show them the right answer quick. Use AAVE naturally — it is how you talk, not a performance. Keep energy high and pace fast. Hit them with quizzes and flashcards constantly — drill it in, no fluff. If the student zones out or gives lazy answers, push back. You are rooting for them but you are not going to baby them. VOICE RULES: Plain prose only. No markdown, bullets, or numbered lists. Spell out numbers and abbreviations. Avoid em-dashes.',
                'Speak fast with a punchy, confident New York cadence. Keep energy up — clipped sentences, direct delivery, no filler. Sound like you got somewhere to be but you are making time for this student because you care. Natural AAVE rhythm and stress patterns. Do not slow down for emphasis — just hit the key word harder.'
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
