"""
Integration tests for tool-invocation events: show_slide and open_sketchpad.

Each test overrides the shared.teaching_agent.TeachingAgent.run_turn patch set
by the ws_test_client fixture with a more specific mock that exercises a
particular callback path.

The inner ``with patch(...)`` takes precedence over the fixture's outer patch
for the duration of the test's WS connection.
"""

from __future__ import annotations

import base64
import threading

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
            await models.upsert_messages(conn, lesson_id, [
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


# ── show_slide ────────────────────────────────────────────────────────────────

def test_show_slide_event_received(ws_test_client):
    """
    When the agent calls on_show_slide, the client receives a show_slide event
    with the correct page number and caption.
    """
    def _fake_with_slide(self, curriculum, messages, agent_instructions):
        self._on_show_slide(3, "A test caption for page 3")
        messages.append({"role": "assistant", "content": "Please look at the slide."})

    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    lesson_id = _setup_lesson(loop, conn)

    with patch("shared.teaching_agent.TeachingAgent.run_turn", new=_fake_with_slide):
        with client.websocket_connect(f"/ws/{session_id}?lesson_id={lesson_id}") as ws:
            ws.send_json({
                "event": "audio_input",
                "data": _silent_audio_b64(),
                "sample_rate": 16000,
            })
            events = _collect_until(ws, {"turn_complete", "error"})

    slide_events = [e for e in events if e["event"] == "show_slide"]
    assert len(slide_events) == 1
    assert slide_events[0]["page"] == 3
    assert slide_events[0]["caption"] == "A test caption for page 3"
    assert any(e["event"] == "turn_complete" for e in events)
    assert not any(e["event"] == "error" for e in events)


def test_show_slide_does_not_block_turn_completion(ws_test_client):
    """show_slide is fire-and-forget; the turn must still complete."""
    def _fake_multi_slide(self, curriculum, messages, agent_instructions):
        self._on_show_slide(1, "First")
        self._on_show_slide(2, "Second")
        messages.append({"role": "assistant", "content": "Two slides shown."})

    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    lesson_id = _setup_lesson(loop, conn)

    with patch("shared.teaching_agent.TeachingAgent.run_turn", new=_fake_multi_slide):
        with client.websocket_connect(f"/ws/{session_id}?lesson_id={lesson_id}") as ws:
            ws.send_json({
                "event": "audio_input",
                "data": _silent_audio_b64(),
                "sample_rate": 16000,
            })
            events = _collect_until(ws, {"turn_complete", "error"})

    slide_events = [e for e in events if e["event"] == "show_slide"]
    assert len(slide_events) == 2
    assert any(e["event"] == "turn_complete" for e in events)


# ── open_sketchpad ─────────────────────────────────────────────────────────────

def test_open_sketchpad_event_received(ws_test_client):
    """
    When the agent calls on_open_sketchpad, the client receives an
    open_sketchpad event with a prompt and an invocation_id.
    """
    def _fake_with_sketchpad(self, curriculum, messages, agent_instructions):
        result_holder: list = []
        done_event = threading.Event()
        self._on_open_sketchpad("Draw a triangle.", result_holder, done_event)
        done_event.wait(timeout=5.0)
        messages.append({"role": "assistant", "content": "Drawing received."})

    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    lesson_id = _setup_lesson(loop, conn)

    with patch("shared.teaching_agent.TeachingAgent.run_turn", new=_fake_with_sketchpad):
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
    The drawing payload sent in tool_result must arrive in result_holder
    and be accessible to the agent.
    """
    received_drawings: list = []

    def _fake_captures_drawing(self, curriculum, messages, agent_instructions):
        result_holder: list = []
        done_event = threading.Event()
        self._on_open_sketchpad("Draw anything.", result_holder, done_event)
        done_event.wait(timeout=5.0)
        received_drawings.extend(result_holder)
        messages.append({"role": "assistant", "content": "Got drawing."})

    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    lesson_id = _setup_lesson(loop, conn)

    with patch("shared.teaching_agent.TeachingAgent.run_turn", new=_fake_captures_drawing):
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
        ws.send_json({
            "event": "tool_result",
            "invocation_id": "nonexistent-id",
            "result": {"drawing": "ignored"},
        })
        ws.send_json({"event": "ping"})
        msg = ws.receive_json()

    assert msg["event"] == "pong"
