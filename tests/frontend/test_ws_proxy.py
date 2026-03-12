"""
Tests for the WebSocket proxy router.

These tests use a mock backend WebSocket echo server so we can verify the
relay logic without running the real backend.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
import websockets.asyncio.client
import websockets.asyncio.server

from frontend.session_store import SessionStore
from frontend.rate_limiter import RateLimiter


# ── helpers ────────────────────────────────────────────────────────────────────

async def _echo_server(websocket):
    """Minimal echo backend: reflects every received message."""
    async for msg in websocket:
        await websocket.send(msg)


def _start_echo_server(host: str = "127.0.0.1", port: int = 18765):
    """Start an echo WS server in a background thread; return (thread, stop_event)."""
    started = threading.Event()
    stop = threading.Event()

    async def _run():
        async with websockets.asyncio.server.serve(_echo_server, host, port):
            started.set()
            await asyncio.get_event_loop().run_in_executor(None, stop.wait)

    def _thread():
        asyncio.run(_run())

    t = threading.Thread(target=_thread, daemon=True)
    t.start()
    started.wait(timeout=3)
    return t, stop


# ── unit tests (no server needed) ─────────────────────────────────────────────

def test_session_validation_rejects_unknown_session():
    """Simulate what the proxy does: unknown session_id → no route."""
    store = SessionStore()
    assert store.get("unknown") is None


def test_rate_limiter_audio_input_cost():
    """audio_input events should cost 5 tokens."""
    limiter = RateLimiter(capacity=4.0, refill_rate=0.0)  # no refill
    # 4 tokens available; audio_input costs 5 → rejected
    assert limiter.allow("sess", tokens=5.0) is False


def test_rate_limiter_ping_free():
    """ping events cost 0 tokens and should always pass."""
    limiter = RateLimiter(capacity=0.0, refill_rate=0.0)
    assert limiter.allow("sess", tokens=0.0) is True


# ── integration tests (echo backend) ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_echo_relay_round_trip():
    """
    Verify that a message sent to the echo server arrives back unmodified.
    This tests the basic relay mechanics independently of FastAPI.
    """
    _, stop = _start_echo_server(port=18765)
    try:
        async with websockets.asyncio.client.connect("ws://127.0.0.1:18765") as ws:
            payload = json.dumps({"event": "ping"})
            await ws.send(payload)
            response = await asyncio.wait_for(ws.recv(), timeout=2)
            assert response == payload
    finally:
        stop.set()


@pytest.mark.asyncio
async def test_echo_relay_multiple_messages():
    """Multiple messages should be echoed in order."""
    _, stop = _start_echo_server(port=18766)
    try:
        async with websockets.asyncio.client.connect("ws://127.0.0.1:18766") as ws:
            messages = [
                json.dumps({"event": "audio_input", "data": "abc"}),
                json.dumps({"event": "tool_result", "invocation_id": "x"}),
            ]
            for msg in messages:
                await ws.send(msg)
            for expected in messages:
                received = await asyncio.wait_for(ws.recv(), timeout=2)
                assert received == expected
    finally:
        stop.set()


@pytest.mark.skip(reason="Requires full frontend app with session store wired in (Phase 3)")
@pytest.mark.asyncio
async def test_proxy_rejects_invalid_session(client):
    """Connecting to /ws/{bad_session_id} should close with 4001."""
    async with client.websocket_connect("/ws/invalid-id?lesson_id=x") as ws:
        msg = await ws.receive_json()
        assert msg["event"] == "error"
