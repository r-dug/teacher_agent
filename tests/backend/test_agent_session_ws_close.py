"""Tests for websocket close handling in BackendAgentSession."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future

from backend.services.agent import BackendAgentSession


def test_fire_swallow_send_after_close_error():
    loop = asyncio.new_event_loop()

    async def _send(_event: dict) -> None:
        return

    sess = BackendAgentSession(
        send=_send,
        loop=loop,
        tts_provider=None,
        fallback_tts_provider=None,
    )

    called = {"n": 0}
    original = asyncio.run_coroutine_threadsafe

    def _fake_run_coroutine_threadsafe(_coro, _loop):
        called["n"] += 1
        close = getattr(_coro, "close", None)
        if callable(close):
            close()
        fut: Future = Future()
        fut.set_exception(
            RuntimeError(
                "Unexpected ASGI message 'websocket.send', after sending 'websocket.close' or response already completed."
            )
        )
        return fut

    asyncio.run_coroutine_threadsafe = _fake_run_coroutine_threadsafe  # type: ignore[assignment]
    try:
        # First send marks closed and swallows error.
        sess._fire(_send({}))
        assert called["n"] == 1
        # Subsequent sends are no-ops.
        sess._fire(_send({}))
        assert called["n"] == 1
    finally:
        asyncio.run_coroutine_threadsafe = original  # type: ignore[assignment]
        loop.close()
