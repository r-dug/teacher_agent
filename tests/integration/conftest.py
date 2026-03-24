"""
Integration test fixtures.

ws_test_client: Starlette TestClient wired to the PostgreSQL test database with
  ML models mocked out.  Use this for WebSocket protocol tests.

Design
------
The TestClient runs the ASGI app in a background portal event loop (anyio).
Each request inside the portal loop gets its own fresh asyncpg connection via
the overridden db.get dependency.

A *separate* asyncpg connection (``conn``) is opened in the test's own mini
event loop solely for setup and post-assertion DB queries.  Both connections
talk to the same PostgreSQL database so data is shared.

Between tests, all tables are truncated to give a clean slate.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql://localhost/pdf_to_audio_test"
)

_SCHEMA_SQL = (
    Path(__file__).parent.parent.parent / "backend" / "db" / "schema.sql"
).read_text()

_TRUNCATE_SQL = """
TRUNCATE TABLE
    messages, enrollment_assets, lesson_enrollments, lesson_sections,
    section_assets, course_chapter_lessons, course_decomposition_job_items,
    course_decomposition_jobs, course_advisor_sessions, course_chapter_drafts,
    course_source_files, textbook_toc_cache, decomposition_cache,
    course_publish_copies, lesson_publish_copies, lessons, courses,
    point_events, user_points, password_reset_tokens, email_verifications,
    upload_tokens, sessions, personas, usage_raw, usage_minutes, usage_hours, users
RESTART IDENTITY CASCADE
"""


@pytest.fixture
def ws_test_client(tmp_path):
    """
    Yield (TestClient, asyncpg.Connection, asyncio.EventLoop).

    - TestClient  : Starlette sync WS client.
    - conn        : Setup/verification connection (use with loop.run_until_complete).
    - loop        : Mini event loop for running async DB helpers in test code.

    Patches applied for the duration of the fixture:
      - backend.routers.ws_session.transcribe  → AsyncMock returning "hello teacher"
      - backend.services.agents.teacher_agent.TeacherAgent.run_turn → fast sync mock
      - app_state.stt_model                    → MagicMock
      - app_state.kokoro_pipeline              → None
    """
    from starlette.testclient import TestClient
    from backend.main import app
    from backend.db import connection as db, models
    from backend import app_state as _app_state_mod

    # ── setup loop: apply schema, truncate for clean state, keep setup conn ────

    loop = asyncio.new_event_loop()

    async def _setup():
        conn = await asyncpg.connect(TEST_DATABASE_URL)
        # Terminate any other connections that might block TRUNCATE
        await conn.execute("""
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = current_database() AND pid <> pg_backend_pid()
        """)
        # Ensure schema exists (idempotent)
        await conn.execute(_SCHEMA_SQL)
        # Clean state
        await conn.execute(_TRUNCATE_SQL)
        # Seed anonymous user and personas
        await conn.execute(
            "INSERT INTO users (id, display_name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            db.ANON_USER_ID, "Anonymous",
        )
        await models.seed_personas(conn)
        return conn

    conn = loop.run_until_complete(_setup())

    # ── override db.get: fresh connection per request in the portal loop ───────

    async def _override_db():
        c = await asyncpg.connect(TEST_DATABASE_URL)
        try:
            yield c
        finally:
            await c.close()

    app.dependency_overrides[db.get] = _override_db

    # ── mock heavyweight models ────────────────────────────────────────────────

    original_stt = _app_state_mod.app_state.stt_model
    original_kokoro = _app_state_mod.app_state.kokoro_pipeline
    _app_state_mod.app_state.stt_model = MagicMock()
    _app_state_mod.app_state.kokoro_pipeline = None

    # ── patch STT + teaching agent ─────────────────────────────────────────────

    def _fake_run_turn(self, curriculum, messages, agent_instructions, lesson_goal=None):
        messages.append({"role": "assistant", "content": "Let's begin!"})

    with patch(
        "backend.routers.ws_session.transcribe",
        new=AsyncMock(return_value="hello teacher"),
    ):
        with patch(
            "backend.services.agents.teacher_agent.TeacherAgent.run_turn",
            new=_fake_run_turn,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            yield client, conn, loop

    # ── teardown ───────────────────────────────────────────────────────────────

    app.dependency_overrides.clear()
    _app_state_mod.app_state.stt_model = original_stt
    _app_state_mod.app_state.kokoro_pipeline = original_kokoro
    loop.run_until_complete(conn.close())
    loop.close()
