"""
WebSocket proxy: relay messages between the client and the backend.

Connection flow:
  1. Client connects to ws://frontend:8000/ws/{session_id}?lesson_id=...
  2. Frontend validates session_id and rate-limits the connection.
  3. Frontend opens a mirrored WS connection to the backend.
  4. All frames are relayed bidirectionally until either side disconnects.

The frontend never inspects message content — it is a transparent relay after
the initial auth check.  This keeps auth logic in one place (here) and keeps
the backend simple (it trusts all traffic from the frontend).
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.parse

import websockets.asyncio.client
import websockets.exceptions
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import settings
from ..rate_limiter import limiter
from ..session_store import store

router = APIRouter(tags=["ws"])

# Audio input events are expensive (STT → LLM → TTS); cost more tokens.
_EVENT_COSTS: dict[str, float] = {
    "audio_input": 5.0,
    "tool_result": 1.0,
    "cancel_turn": 1.0,
    "set_instructions": 1.0,
    "reconnect": 1.0,
    "ping": 0.0,
}
_DEFAULT_COST = 1.0


@router.websocket("/ws/{session_id}")
async def ws_proxy(
    websocket: WebSocket,
    session_id: str,
    lesson_id: str,
):
    await websocket.accept()

    # ── auth ──────────────────────────────────────────────────────────────────

    entry = store.get(session_id)
    if entry is None:
        await websocket.send_json({"event": "error", "message": "Invalid session"})
        await websocket.close(code=4001)
        return

    # Initial connection costs 1 token
    if not limiter.allow(session_id, tokens=1.0):
        await websocket.send_json({"event": "error", "message": "Rate limited"})
        await websocket.close(code=4029)
        return

    # ── connect to backend ─────────────────────────────────────────────────────

    _UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)
    if not _UUID_RE.match(lesson_id):
        await websocket.send_json({"event": "error", "message": "Invalid lesson ID"})
        await websocket.close(code=4004)
        return

    backend_url = (
        f"{settings.BACKEND_WS}/ws/{session_id}"
        f"?{urllib.parse.urlencode({'lesson_id': lesson_id})}"
    )

    try:
        extra_headers = {}
        if settings.BACKEND_SHARED_SECRET:
            extra_headers["X-Internal-Token"] = settings.BACKEND_SHARED_SECRET

        async with websockets.asyncio.client.connect(
            backend_url,
            ping_interval=20,
            ping_timeout=10,
            max_size=4 * 1024 * 1024,  # 4 MB — audio sub-chunks are ≤350 KB after base64
            additional_headers=extra_headers,
        ) as backend_ws:
            await _relay(websocket, backend_ws, session_id)

    except websockets.exceptions.ConnectionClosedError:
        pass  # backend disconnected normally
    except OSError:
        try:
            await websocket.send_json({
                "event": "error",
                "message": "Backend unavailable — is the backend server running?",
            })
        except Exception:
            pass
    except WebSocketDisconnect:
        pass


async def _relay(
    client_ws: WebSocket,
    backend_ws: websockets.WebSocketClientProtocol,
    session_id: str,
) -> None:
    """Bidirectional relay loop between client and backend."""

    async def client_to_backend() -> None:
        """Forward client frames to the backend, applying per-event rate limiting."""
        async for raw in client_ws.iter_text():
            # Determine cost of this event for rate limiting
            cost = _DEFAULT_COST
            try:
                msg = json.loads(raw)
                cost = _EVENT_COSTS.get(msg.get("event", ""), _DEFAULT_COST)
            except (json.JSONDecodeError, AttributeError):
                pass

            if cost > 0 and not limiter.allow(session_id, tokens=cost):
                await client_ws.send_json({
                    "event": "error",
                    "message": "Rate limited — please wait before sending another message",
                })
                continue

            await backend_ws.send(raw)

    async def backend_to_client() -> None:
        """Forward backend frames to the client unchanged."""
        async for raw in backend_ws:
            if isinstance(raw, bytes):
                await client_ws.send_bytes(raw)
            else:
                await client_ws.send_text(raw)

    # Run both directions concurrently; stop as soon as either side closes.
    client_task = asyncio.create_task(client_to_backend())
    backend_task = asyncio.create_task(backend_to_client())

    try:
        done, pending = await asyncio.wait(
            {client_task, backend_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
    except Exception:
        client_task.cancel()
        backend_task.cancel()
