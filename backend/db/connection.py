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
    # Tables used for admin course publication (idempotent fan-out copies).
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS course_publish_copies (
               source_course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
               target_user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
               target_course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
               created_at       TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
               PRIMARY KEY (source_course_id, target_user_id)
           )"""
    )
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS lesson_publish_copies (
               source_lesson_id TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
               target_user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
               target_lesson_id TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
               created_at       TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
               PRIMARY KEY (source_lesson_id, target_user_id)
           )"""
    )
    # Textbook authoring + hash caches.
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS course_source_files (
               course_id   TEXT PRIMARY KEY REFERENCES courses(id) ON DELETE CASCADE,
               user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
               pdf_hash    TEXT NOT NULL,
               pdf_path    TEXT NOT NULL,
               page_count  INTEGER NOT NULL,
               toc_json    TEXT NOT NULL DEFAULT '[]',
               created_at  TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_course_source_files_pdf_hash ON course_source_files(pdf_hash)"
    )
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS course_chapter_drafts (
               id          TEXT PRIMARY KEY,
               course_id   TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
               idx         INTEGER NOT NULL,
               title       TEXT NOT NULL,
               page_start  INTEGER NOT NULL,
               page_end    INTEGER NOT NULL,
               included    INTEGER NOT NULL DEFAULT 1,
               created_at  TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
               UNIQUE(course_id, idx)
           )"""
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_course_chapter_drafts_course ON course_chapter_drafts(course_id, idx)"
    )
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS textbook_toc_cache (
               pdf_hash      TEXT PRIMARY KEY,
               page_count    INTEGER NOT NULL,
               toc_json      TEXT NOT NULL DEFAULT '[]',
               chapters_json TEXT NOT NULL DEFAULT '[]',
               created_at    TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS decomposition_cache (
               cache_key       TEXT PRIMARY KEY,
               pdf_hash        TEXT NOT NULL,
               page_start      INTEGER NOT NULL,
               page_end        INTEGER NOT NULL,
               objectives_hash TEXT NOT NULL DEFAULT '',
               model           TEXT NOT NULL DEFAULT '',
               prompt_version  TEXT NOT NULL DEFAULT '',
               sections_json   TEXT NOT NULL DEFAULT '[]',
               created_at      TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    await conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_decomposition_cache_lookup
           ON decomposition_cache(pdf_hash, page_start, page_end, objectives_hash, model, prompt_version)"""
    )
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS course_chapter_lessons (
               course_id    TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
               chapter_id   TEXT NOT NULL REFERENCES course_chapter_drafts(id) ON DELETE CASCADE,
               lesson_id    TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
               created_at   TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
               PRIMARY KEY (course_id, chapter_id)
           )"""
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_course_chapter_lessons_lesson ON course_chapter_lessons(lesson_id)"
    )
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS course_advisor_sessions (
               course_id          TEXT PRIMARY KEY REFERENCES courses(id) ON DELETE CASCADE,
               user_id            TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
               transcript_json    TEXT NOT NULL DEFAULT '[]',
               objectives_prompt  TEXT,
               status             TEXT NOT NULL DEFAULT 'draft',
               created_at         TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS course_decomposition_jobs (
               id                TEXT PRIMARY KEY,
               course_id         TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
               user_id           TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
               status            TEXT NOT NULL DEFAULT 'queued',
               objectives_prompt TEXT NOT NULL DEFAULT '',
               total_items       INTEGER NOT NULL DEFAULT 0,
               completed_items   INTEGER NOT NULL DEFAULT 0,
               failed_items      INTEGER NOT NULL DEFAULT 0,
               notify_session_id TEXT,
               error             TEXT,
               created_at        TEXT NOT NULL DEFAULT (datetime('now')),
               started_at        TEXT,
               finished_at       TEXT,
               updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_course_decomp_jobs_course ON course_decomposition_jobs(course_id, created_at)"
    )
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS course_decomposition_job_items (
               id           TEXT PRIMARY KEY,
               job_id       TEXT NOT NULL REFERENCES course_decomposition_jobs(id) ON DELETE CASCADE,
               chapter_id   TEXT NOT NULL REFERENCES course_chapter_drafts(id) ON DELETE CASCADE,
               idx          INTEGER NOT NULL,
               title        TEXT NOT NULL,
               page_start   INTEGER NOT NULL,
               page_end     INTEGER NOT NULL,
               lesson_id    TEXT,
               cache_key    TEXT,
               status       TEXT NOT NULL DEFAULT 'queued',
               error        TEXT,
               created_at   TEXT NOT NULL DEFAULT (datetime('now')),
               updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_course_decomp_items_job ON course_decomposition_job_items(job_id, idx)"
    )
    # Per-user generated images produced during teaching (enrollment-level state).
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS enrollment_assets (
               id            TEXT PRIMARY KEY,
               enrollment_id TEXT NOT NULL REFERENCES lesson_enrollments(id) ON DELETE CASCADE,
               section_idx   INTEGER NOT NULL,
               asset_type    TEXT NOT NULL DEFAULT 'ai_image',
               image_path    TEXT,
               prompt        TEXT,
               revised_prompt TEXT,
               tool_use_id   TEXT,
               idx           INTEGER NOT NULL DEFAULT 0,
               created_at    TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )
    await conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_enrollment_assets_enrollment
           ON enrollment_assets(enrollment_id, section_idx)"""
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
