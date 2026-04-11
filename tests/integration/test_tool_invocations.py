"""
Integration tests for tool-invocation events: open_sketchpad and others.

Each test overrides the backend.services.agents.teacher_agent.TeacherAgent.run_turn patch set
by the ws_test_client fixture with a more specific mock that exercises a
particular callback path.

The inner ``with patch(...)`` takes precedence over the fixture's outer patch
for the duration of the test's WS connection.
"""

from __future__ import annotations

import base64

import numpy as np
import pytest
from unittest.mock import patch

from backend.db import connection as db, models


# ── helpers (duplicated from test_ws_session for independence) ─────────────────

def _silent_audio_b64(n_samples: int = 16000) -> str:
    return base64.b64encode(np.zeros(n_samples, dtype=np.float32).tobytes()).decode()


def _setup_lesson(loop, conn, *, with_sections=True, with_messages=True) -> str:
    """Create a test lesson with prior messages (no auto-start)."""
    async def _go():
        lesson_id = await models.create_lesson(conn, db.ANON_USER_ID, "Tool Test Lesson")
        if with_sections:
            await models.upsert_sections(conn, lesson_id, [{
                "title": "Section 1",
                "content": "Content for tool invocation testing.",
                "key_concepts": ["tool"],
                "page_start": 1,
                "page_end": 5,
            }])
        if with_messages:
            # Pre-seed a message so auto-start does not fire
            enrollment = await models.get_or_create_enrollment(conn, lesson_id, db.ANON_USER_ID)
            await models.upsert_messages(conn, enrollment["id"], [
                {"role": "assistant", "content": "Let's begin."},
            ])
        return lesson_id
    return loop.run_until_complete(_go())


def _setup_session(loop, conn) -> str:
    return loop.run_until_complete(models.create_session(conn))


def _collect_until(ws, terminal_events: set[str], max_messages: int = 30) -> list[dict]:
    events = []
    for _ in range(max_messages):
        msg = ws.receive_json()
        events.append(msg)
        if msg.get("event") in terminal_events:
            break
    return events


# ── open_sketchpad ─────────────────────────────────────────────────────────────

def test_open_sketchpad_event_received(ws_test_client):
    """
    When the agent calls on_open_sketchpad, the client receives an
    open_sketchpad event with a prompt and an invocation_id.
    """
    async def _fake_with_sketchpad(self, curriculum, messages, agent_instructions, lesson_goal=None, turn_id=""):
        # Plan B Commit 4: all 13 client-interactive tools share one
        # callback, ``on_open_interactive_tool``, which takes a fully-built
        # event dict and returns the raw client result dict.
        import uuid as _uuid
        await self._callbacks.on_open_interactive_tool({
            "event": "open_sketchpad",
            "prompt": "Draw a triangle.",
            "invocation_id": str(_uuid.uuid4()),
        })
        messages.append({"role": "assistant", "content": "Drawing received."})

    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    lesson_id = _setup_lesson(loop, conn)

    with patch("backend.services.agents.teacher_agent.TeacherAgent.run_turn", new=_fake_with_sketchpad):
        with client.websocket_connect(f"/ws/{session_id}?lesson_id={lesson_id}") as ws:
            ws.send_json({
                "event": "audio_input",
                "data": _silent_audio_b64(),
                "sample_rate": 16000,
            })

            # Collect until we see open_sketchpad
            events_pre = _collect_until(ws, {"open_sketchpad", "error"})

            sketchpad_events = [e for e in events_pre if e["event"] == "open_sketchpad"]
            assert len(sketchpad_events) == 1
            assert sketchpad_events[0]["prompt"] == "Draw a triangle."
            assert "invocation_id" in sketchpad_events[0]

            # Unblock the agent by sending tool_result
            ws.send_json({
                "event": "tool_result",
                "invocation_id": sketchpad_events[0]["invocation_id"],
                "result": {"drawing": "base64encodedpng"},
            })

            events_post = _collect_until(ws, {"turn_complete", "error"})

    all_events = events_pre + events_post
    assert any(e["event"] == "turn_complete" for e in all_events)
    assert not any(e["event"] == "error" for e in all_events)


def test_open_sketchpad_drawing_reaches_agent(ws_test_client):
    """
    The drawing payload sent in tool_result must arrive at the agent (now
    via the awaited callback's return value rather than a result_holder).
    """
    received_drawings: list = []

    async def _fake_captures_drawing(self, curriculum, messages, agent_instructions, lesson_goal=None, turn_id=""):
        # Plan B Commit 4: unified interactive callback.
        import uuid as _uuid
        raw = await self._callbacks.on_open_interactive_tool({
            "event": "open_sketchpad",
            "prompt": "Draw anything.",
            "invocation_id": str(_uuid.uuid4()),
        })
        received_drawings.append((raw or {}).get("drawing"))
        messages.append({"role": "assistant", "content": "Got drawing."})

    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    lesson_id = _setup_lesson(loop, conn)

    with patch("backend.services.agents.teacher_agent.TeacherAgent.run_turn", new=_fake_captures_drawing):
        with client.websocket_connect(f"/ws/{session_id}?lesson_id={lesson_id}") as ws:
            ws.send_json({
                "event": "audio_input",
                "data": _silent_audio_b64(),
                "sample_rate": 16000,
            })

            events_pre = _collect_until(ws, {"open_sketchpad", "error"})
            inv_id = next(
                e["invocation_id"]
                for e in events_pre
                if e["event"] == "open_sketchpad"
            )

            ws.send_json({
                "event": "tool_result",
                "invocation_id": inv_id,
                "result": {"drawing": "EXPECTED_DRAWING_DATA"},
            })
            _collect_until(ws, {"turn_complete", "error"})

    assert received_drawings == ["EXPECTED_DRAWING_DATA"]


def test_invalid_invocation_id_is_harmless(ws_test_client):
    """
    tool_result with an unknown invocation_id must not crash the session.
    ping still works afterwards.
    """
    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    lesson_id = _setup_lesson(loop, conn)

    with client.websocket_connect(f"/ws/{session_id}?lesson_id={lesson_id}") as ws:
        # Drain initial connect-phase events (decompose_complete + history)
        _collect_until(ws, {"decompose_complete"})
        _collect_until(ws, {"history"})
        ws.send_json({
            "event": "tool_result",
            "invocation_id": "nonexistent-id",
            "result": {"drawing": "ignored"},
        })
        ws.send_json({"event": "ping"})
        msg = ws.receive_json()

    assert msg["event"] == "pong"
