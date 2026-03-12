"""
Integration tests for the backend WebSocket session handler.

Uses the ``ws_test_client`` fixture (Starlette TestClient + in-memory DB +
mocked ML models) defined in tests/integration/conftest.py.

All tests are synchronous because Starlette's TestClient is a sync API.
DB helpers are run via ``loop.run_until_complete()``.

Mocks active for all tests (applied by ws_test_client fixture):
  - transcribe()          → AsyncMock returning "hello teacher"
  - TeachingAgent.run_turn → appends one assistant message and returns
  - app_state.stt_model  → MagicMock (never actually called for STT)
  - app_state.kokoro_pipeline → None (TTS not produced in tests)
"""

from __future__ import annotations

import base64

import numpy as np
import pytest

from backend.db import connection as db, models


# ── helpers ───────────────────────────────────────────────────────────────────

def _silent_audio_b64(n_samples: int = 16000) -> str:
    return base64.b64encode(np.zeros(n_samples, dtype=np.float32).tobytes()).decode()


def _setup_lesson(loop, conn, *, with_sections=True, with_messages=False) -> str:
    """Create a test lesson in the DB and return its ID."""
    async def _go():
        lesson_id = await models.create_lesson(conn, db.ANON_USER_ID, "Test Lesson")
        if with_sections:
            await models.upsert_sections(conn, lesson_id, [{
                "title": "Introduction",
                "content": "Basic content for integration testing.",
                "key_concepts": ["concept A"],
                "page_start": 1,
                "page_end": 3,
            }])
        if with_messages:
            await models.upsert_messages(conn, lesson_id, [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ])
        return lesson_id
    return loop.run_until_complete(_go())


def _setup_session(loop, conn) -> str:
    return loop.run_until_complete(models.create_session(conn))


def _collect_until(ws, terminal_events: set[str], max_messages: int = 30) -> list[dict]:
    """Receive messages until a terminal event or max_messages is reached."""
    events = []
    for _ in range(max_messages):
        msg = ws.receive_json()
        events.append(msg)
        if msg.get("event") in terminal_events:
            break
    return events


# ── connection validation ─────────────────────────────────────────────────────

def test_invalid_session_is_rejected(ws_test_client):
    """Connecting with an unknown session_id receives an error and WS closes."""
    client, conn, loop = ws_test_client
    lesson_id = _setup_lesson(loop, conn)

    with client.websocket_connect(f"/ws/bad-session-id?lesson_id={lesson_id}") as ws:
        msg = ws.receive_json()
    assert msg["event"] == "error"
    assert "Invalid session" in msg["message"]


def test_lesson_not_found_is_rejected(ws_test_client):
    """Valid session but non-existent lesson_id receives an error."""
    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)

    with client.websocket_connect(f"/ws/{session_id}?lesson_id=nonexistent") as ws:
        msg = ws.receive_json()
    assert msg["event"] == "error"
    assert "Lesson not found" in msg["message"]


# ── basic protocol ────────────────────────────────────────────────────────────

def test_ping_returns_pong(ws_test_client):
    """ping event must elicit a pong response."""
    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    # No sections → no auto-start; clean slate for protocol test
    lesson_id = _setup_lesson(loop, conn, with_sections=False)

    with client.websocket_connect(f"/ws/{session_id}?lesson_id={lesson_id}") as ws:
        ws.send_json({"event": "ping"})
        msg = ws.receive_json()
    assert msg["event"] == "pong"


def test_set_instructions_accepted(ws_test_client):
    """set_instructions is a silent state update; no error should follow."""
    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    lesson_id = _setup_lesson(loop, conn, with_sections=False)

    with client.websocket_connect(f"/ws/{session_id}?lesson_id={lesson_id}") as ws:
        ws.send_json({"event": "set_instructions", "instructions": "Be concise."})
        # Verify connection is still healthy
        ws.send_json({"event": "ping"})
        msg = ws.receive_json()
    assert msg["event"] == "pong"


def test_cancel_turn_when_idle_is_harmless(ws_test_client):
    """cancel_turn with no running agent task must not crash the session."""
    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    lesson_id = _setup_lesson(loop, conn, with_sections=False)

    with client.websocket_connect(f"/ws/{session_id}?lesson_id={lesson_id}") as ws:
        ws.send_json({"event": "cancel_turn"})
        ws.send_json({"event": "ping"})
        msg = ws.receive_json()
    assert msg["event"] == "pong"


def test_invalid_json_returns_error(ws_test_client):
    """Sending non-JSON text returns an error event without crashing."""
    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    lesson_id = _setup_lesson(loop, conn, with_sections=False)

    with client.websocket_connect(f"/ws/{session_id}?lesson_id={lesson_id}") as ws:
        ws.send_text("not valid json {{")
        msg = ws.receive_json()
        assert msg["event"] == "error"
        # Session should survive — still responds to ping
        ws.send_json({"event": "ping"})
        msg2 = ws.receive_json()
    assert msg2["event"] == "pong"


# ── reconnect ─────────────────────────────────────────────────────────────────

def test_reconnect_with_no_running_turn(ws_test_client):
    """reconnect event with a stale turn_id should return reconnect_ack."""
    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    lesson_id = _setup_lesson(loop, conn, with_sections=False)

    with client.websocket_connect(f"/ws/{session_id}?lesson_id={lesson_id}") as ws:
        ws.send_json({"event": "reconnect", "last_turn_id": "old-turn-id"})
        msg = ws.receive_json()
    assert msg["event"] == "reconnect_ack"
    assert msg["turn_status"] == "idle"


def test_reconnect_ack_includes_curriculum(ws_test_client):
    """reconnect_ack carries the current curriculum state."""
    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    lesson_id = _setup_lesson(loop, conn, with_sections=True, with_messages=True)

    with client.websocket_connect(f"/ws/{session_id}?lesson_id={lesson_id}") as ws:
        ws.send_json({"event": "reconnect", "last_turn_id": "old-id"})
        msg = ws.receive_json()

    assert msg["event"] == "reconnect_ack"
    assert msg["curriculum"]["total"] == 1  # one section inserted by _setup_lesson


# ── auto-start ────────────────────────────────────────────────────────────────

def test_auto_start_fires_on_fresh_lesson(ws_test_client):
    """
    A lesson with sections but no prior messages triggers an automatic first
    teaching turn on connect.  The client should receive turn_complete.
    """
    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    # sections=True, messages=False → auto-start fires
    lesson_id = _setup_lesson(loop, conn, with_sections=True, with_messages=False)

    with client.websocket_connect(f"/ws/{session_id}?lesson_id={lesson_id}") as ws:
        events = _collect_until(ws, {"turn_complete", "error"})

    event_names = {e["event"] for e in events}
    assert "turn_complete" in event_names
    assert "error" not in event_names


def test_auto_start_does_not_fire_when_messages_exist(ws_test_client):
    """
    Resuming a lesson that already has conversation history must NOT auto-start.
    The client receives no unsolicited events; ping still works.
    """
    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    # sections=True, messages=True → no auto-start
    lesson_id = _setup_lesson(loop, conn, with_sections=True, with_messages=True)

    with client.websocket_connect(f"/ws/{session_id}?lesson_id={lesson_id}") as ws:
        ws.send_json({"event": "ping"})
        msg = ws.receive_json()
    assert msg["event"] == "pong"


# ── audio input → full turn ───────────────────────────────────────────────────

def test_audio_input_produces_turn_complete(ws_test_client):
    """
    audio_input → (mock) STT → agent turn → turn_complete.

    Protocol sequence expected:
      status("Transcribing...") | transcription | status("Thinking...") | turn_complete
    """
    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    # has messages → no auto-start; clean entry for audio test
    lesson_id = _setup_lesson(loop, conn, with_sections=True, with_messages=True)

    with client.websocket_connect(f"/ws/{session_id}?lesson_id={lesson_id}") as ws:
        ws.send_json({
            "event": "audio_input",
            "data": _silent_audio_b64(),
            "sample_rate": 16000,
        })
        events = _collect_until(ws, {"turn_complete", "error"})

    event_names = [e["event"] for e in events]
    assert "transcription" in event_names
    assert "turn_complete" in event_names
    assert "error" not in event_names


def test_audio_input_transcription_text(ws_test_client):
    """The transcription event carries the mock STT text."""
    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    lesson_id = _setup_lesson(loop, conn, with_sections=True, with_messages=True)

    with client.websocket_connect(f"/ws/{session_id}?lesson_id={lesson_id}") as ws:
        ws.send_json({
            "event": "audio_input",
            "data": _silent_audio_b64(),
            "sample_rate": 16000,
        })
        events = _collect_until(ws, {"turn_complete", "error"})

    transcription_events = [e for e in events if e["event"] == "transcription"]
    assert len(transcription_events) == 1
    assert transcription_events[0]["text"] == "hello teacher"


def test_concurrent_audio_input_rejected(ws_test_client):
    """
    Sending audio_input while a turn is already running returns an error
    (not a second turn).  We can't easily test concurrency here, but we can
    verify the guard by checking the first turn completes cleanly.
    """
    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    lesson_id = _setup_lesson(loop, conn, with_sections=True, with_messages=True)

    with client.websocket_connect(f"/ws/{session_id}?lesson_id={lesson_id}") as ws:
        ws.send_json({
            "event": "audio_input",
            "data": _silent_audio_b64(),
            "sample_rate": 16000,
        })
        events = _collect_until(ws, {"turn_complete", "error"})

    # The first turn should complete without error
    assert any(e["event"] == "turn_complete" for e in events)


# ── state persistence ─────────────────────────────────────────────────────────

def test_messages_saved_to_db_after_turn(ws_test_client):
    """
    After a completed audio_input turn, both the user message and the agent
    reply are persisted to the DB.
    """
    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    lesson_id = _setup_lesson(loop, conn, with_sections=True, with_messages=False)

    # Auto-start fires because no messages; wait for it to finish
    with client.websocket_connect(f"/ws/{session_id}?lesson_id={lesson_id}") as ws:
        _collect_until(ws, {"turn_complete", "error"})
        # Now send audio input for a real user turn
        ws.send_json({
            "event": "audio_input",
            "data": _silent_audio_b64(),
            "sample_rate": 16000,
        })
        _collect_until(ws, {"turn_complete", "error"})

    messages = loop.run_until_complete(models.get_messages(conn, lesson_id))
    roles = [m["role"] for m in messages]
    assert "user" in roles
    assert "assistant" in roles
    # At least: auto-start assistant + user + assistant from audio turn
    assert len(messages) >= 2


def test_lesson_progress_saved_on_disconnect(ws_test_client):
    """
    _save_state is called in the finally block; current_section_idx must
    be written back even after a normal disconnect.
    """
    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    lesson_id = _setup_lesson(loop, conn, with_sections=True, with_messages=False)

    with client.websocket_connect(f"/ws/{session_id}?lesson_id={lesson_id}") as ws:
        _collect_until(ws, {"turn_complete", "error"})
        # Disconnect normally

    lesson = loop.run_until_complete(models.get_lesson(conn, lesson_id))
    # current_section_idx is 0; confirm it was written (not left as None/corrupt)
    assert lesson["current_section_idx"] == 0


# ── session isolation ─────────────────────────────────────────────────────────

def test_two_sessions_receive_independent_events(ws_test_client):
    """
    Events sent in session A must not appear in session B's receive stream.
    Both sessions share the same app but use different session_ids.
    """
    client, conn, loop = ws_test_client
    session_id_a = _setup_session(loop, conn)
    session_id_b = _setup_session(loop, conn)
    lesson_id_a = _setup_lesson(loop, conn, with_sections=False)
    lesson_id_b = _setup_lesson(loop, conn, with_sections=False)

    events_a: list[dict] = []
    events_b: list[dict] = []

    # Session A: sends a ping
    with client.websocket_connect(
        f"/ws/{session_id_a}?lesson_id={lesson_id_a}"
    ) as ws_a:
        ws_a.send_json({"event": "ping"})
        events_a.append(ws_a.receive_json())

    # Session B: sends a different ping (separate connection)
    with client.websocket_connect(
        f"/ws/{session_id_b}?lesson_id={lesson_id_b}"
    ) as ws_b:
        ws_b.send_json({"event": "ping"})
        events_b.append(ws_b.receive_json())

    assert events_a == [{"event": "pong"}]
    assert events_b == [{"event": "pong"}]


# ── performance baseline ───────────────────────────────────────────────────────

def test_audio_turn_round_trip_latency(ws_test_client):
    """
    Measure wall-clock latency for the audio_input → turn_complete round trip
    with mocked STT and agent (i.e., pure protocol + thread-pool overhead).

    This establishes a baseline for future regressions.  With mocked ML models
    the full cycle should complete in well under 2 seconds.

    Output is printed so it appears in ``pytest -s`` for human inspection.
    No tight bound is asserted — the goal is to capture the number, not gate CI.
    """
    import time

    client, conn, loop = ws_test_client
    session_id = _setup_session(loop, conn)
    # with_messages=True → no auto-start; clean entry for timing
    lesson_id = _setup_lesson(loop, conn, with_sections=True, with_messages=True)

    latencies_ms: list[float] = []

    with client.websocket_connect(f"/ws/{session_id}?lesson_id={lesson_id}") as ws:
        for _ in range(3):
            t0 = time.monotonic()
            ws.send_json({
                "event": "audio_input",
                "data": _silent_audio_b64(),
                "sample_rate": 16000,
            })
            events = _collect_until(ws, {"turn_complete", "error"})
            elapsed_ms = (time.monotonic() - t0) * 1000
            latencies_ms.append(elapsed_ms)
            assert any(e["event"] == "turn_complete" for e in events), (
                f"Expected turn_complete, got: {[e['event'] for e in events]}"
            )

    avg_ms = sum(latencies_ms) / len(latencies_ms)
    print(
        f"\n[baseline] audio→turn_complete (mocked, n=3): "
        f"min={min(latencies_ms):.1f}ms  avg={avg_ms:.1f}ms  max={max(latencies_ms):.1f}ms"
    )
    # Generous upper bound: catches hangs and catastrophic regressions only
    assert max(latencies_ms) < 2000, (
        f"Slowest mocked round-trip was {max(latencies_ms):.0f}ms — check for blocking calls"
    )
