"""
Integration test fixtures.

ws_test_client: Starlette TestClient wired to a fresh file-based SQLite DB with
  ML models mocked out.  Use this for WebSocket protocol tests.

Design
------
The TestClient runs the ASGI app in a background portal event loop (anyio).
aiosqlite connections are pinned to the event loop that creates them; sharing
one connection between the pytest setup loop and the portal loop causes
call_soon_threadsafe races after ~3 tests.

Solution: the ``db.get`` FastAPI dependency is overridden to create a *fresh*
aiosqlite connection for each request inside the portal loop.  A *separate*
connection (``conn``) is opened in the test's own mini event loop solely for
setup and post-assertion DB queries.  Both connections talk to the same
SQLite file so data is shared via the filesystem.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest


@pytest.fixture
def ws_test_client(tmp_path):
    """
    Yield (TestClient, aiosqlite.Connection, asyncio.EventLoop).

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

    db_path = str(tmp_path / "test.db")
    schema_sql = (
        Path(__file__).parent.parent.parent / "backend" / "db" / "schema.sql"
    ).read_text()

    # ── setup loop: seed DB and keep a connection for test verification ────────

    loop = asyncio.new_event_loop()

    async def _seed():
        c = await aiosqlite.connect(db_path)
        c.row_factory = aiosqlite.Row
        await c.executescript(schema_sql)
        await c.commit()
        await c.execute(
            "INSERT OR IGNORE INTO users (id, display_name) VALUES (?, ?)",
            (db.ANON_USER_ID, "Anonymous"),
        )
        await c.commit()
        await models.seed_personas(c)
        return c

    conn = loop.run_until_complete(_seed())

    # ── override db.get: fresh connection per request in the portal loop ───────
    #
    # Each invocation of this async generator runs inside the ASGI portal's
    # event loop, so aiosqlite futures are always created on the correct loop.

    async def _override_db():
        c = await aiosqlite.connect(db_path)
        c.row_factory = aiosqlite.Row
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
        """Sync mock: append one assistant reply and return immediately."""
        messages.append({"role": "assistant", "content": "Let's begin!"})

    with patch(
        "backend.routers.ws_session.transcribe",
        new=AsyncMock(return_value="hello teacher"),
    ):
        with patch(
            "backend.services.agents.teacher_agent.TeacherAgent.run_turn",
            new=_fake_run_turn,
        ):
            # Skip lifespan by NOT using TestClient as a context manager.
            client = TestClient(app, raise_server_exceptions=True)
            yield client, conn, loop

    # ── teardown ───────────────────────────────────────────────────────────────

    app.dependency_overrides.clear()
    _app_state_mod.app_state.stt_model = original_stt
    _app_state_mod.app_state.kokoro_pipeline = original_kokoro
    loop.run_until_complete(conn.close())
    loop.close()
