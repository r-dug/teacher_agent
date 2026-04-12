"""Global application state: loaded models, session registry."""

from __future__ import annotations

import asyncio
from typing import Optional

from .usage_tracker import UsageTracker as TokenUsageTracker


class AppState:
    """Holds singletons that are expensive to initialise (models, pipelines)."""

    # TTS/STT provider lists — ordered for fallback.  Constructed by
    # build_tts_chain() / build_stt_chain() at startup.
    tts_providers: list = []          # [OpenAITTSProvider, KokoroTTSProvider, ...]
    stt_providers: list = []          # [OpenAISTTProvider, LocalSTTProvider, ...]

    # Legacy fields kept for backward compat during transition.
    # TODO: remove once all callers use tts_providers / stt_providers.
    stt_model: Optional[object] = None
    stt_models: dict = {}
    kokoro_pipeline: Optional[object] = None
    tts_provider: Optional[object] = None
    tts_fallback_provider: Optional[object] = None
    active_tts_provider: str = "kokoro"

    image_provider: Optional[object] = None
    token_tracker: TokenUsageTracker = None   # type: ignore[assignment]


app_state = AppState()
app_state.token_tracker = TokenUsageTracker()


class SessionRegistry:
    """
    Tracks active WebSocket sessions by session_id.

    Background tasks (e.g. PDF decomposition) push events into the per-session
    asyncio.Queue.  The WS handler reads from the queue and forwards to the client.
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue] = {}

    def register(self, session_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._queues[session_id] = q
        return q

    def unregister(self, session_id: str) -> None:
        self._queues.pop(session_id, None)

    async def send(self, session_id: str, event: dict) -> bool:
        """Put an event onto the session's queue.  Returns False if no active session."""
        q = self._queues.get(session_id)
        if q is None:
            return False
        await q.put(event)
        return True

    def send_threadsafe(
        self, session_id: str, event: dict, loop: asyncio.AbstractEventLoop
    ) -> None:
        """Schedule a send from a non-async thread (fire-and-forget)."""
        asyncio.run_coroutine_threadsafe(self.send(session_id, event), loop)


registry = SessionRegistry()
