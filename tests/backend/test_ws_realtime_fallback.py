"""Tests for realtime -> chained fallback in websocket handler."""

from __future__ import annotations

import pytest

from backend.routers import ws_session


class _FakeWebSocket:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def send_json(self, event: dict) -> None:
        self.events.append(event)


class _State:
    def __init__(self) -> None:
        self.voice_arch = "realtime"
        self.phase = "teaching"
        self.agent_task = None
        self.last_turn_id = None
        self.turn_status = "idle"


@pytest.mark.asyncio
async def test_realtime_failure_falls_back_to_chained(monkeypatch):
    ws = _FakeWebSocket()
    state = _State()
    msg = {"data": "AAAA", "sample_rate": 16000}
    calls = {"chained": 0}

    async def _boom(**kwargs):
        raise RuntimeError("realtime down")

    async def _chained(_ws, _msg, st, _conn):
        calls["chained"] += 1
        # Regression check: realtime task marker must be cleared before fallback.
        assert st.agent_task is None

    monkeypatch.setattr(ws_session, "_handle_audio_input_realtime_turn", _boom)
    monkeypatch.setattr(ws_session, "_handle_audio_input_chained", _chained)

    await ws_session._handle_audio_input(ws, msg, state, conn=None)
    assert state.agent_task is not None
    await state.agent_task

    assert calls["chained"] == 1
    assert any(
        e.get("event") == "status" and "Falling back to chained speech path" in e.get("message", "")
        for e in ws.events
    )

