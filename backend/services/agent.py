"""
Backend agent service.

BackendAgentSession wraps a TeachingAgent and wires its callbacks to send
WebSocket events.  All agent work is synchronous and runs in a thread pool
via asyncio.to_thread(); WS sends are scheduled back to the event loop using
asyncio.run_coroutine_threadsafe().

Sketchpad blocking pattern (see design doc):
  1. Agent thread calls on_open_sketchpad callback.
  2. Callback schedules a WS send (open_sketchpad event) on the event loop.
  3. Callback stores a threading.Event + result_holder keyed by invocation_id.
  4. run_turn() in shared/teaching_agent.py calls done_event.wait() — blocks
     the thread pool worker.
  5. The async WS receive loop gets a tool_result message from the client and
     calls handle_tool_result(), which fills result_holder and sets done_event.
  6. Worker thread unblocks and continues.
"""

from __future__ import annotations

import asyncio
import base64
import threading
import uuid
from collections.abc import Callable

import numpy as np

from shared.constants import KOKORO_SAMPLE_RATE, DEFAULT_KOKORO_VOICE
from shared.lesson import Curriculum
from shared.teaching_agent import TeachingAgent


class BackendAgentSession:
    """
    Per-WebSocket-session wrapper around TeachingAgent.

    Parameters
    ----------
    send : async callable that accepts a dict and sends it as JSON over WS.
    loop : the running asyncio event loop (needed for run_coroutine_threadsafe).
    kokoro_pipeline : loaded KPipeline instance (shared across sessions).
    kokoro_voice : Kokoro voice name.
    llm_model : Claude model identifier.
    """

    def __init__(
        self,
        send: Callable,
        loop: asyncio.AbstractEventLoop,
        kokoro_pipeline,
        kokoro_voice: str = DEFAULT_KOKORO_VOICE,
        llm_model: str = "claude-opus-4-6",
        pdf_path: str | None = None,
    ) -> None:
        self._send = send
        self._loop = loop
        self._pdf_path = pdf_path
        # Pending sketchpad invocations: inv_id → (done_event, result_holder)
        self._tool_events: dict[str, tuple[threading.Event, list]] = {}

        self.agent = TeachingAgent(
            llm_model=llm_model,
            kokoro_pipeline=kokoro_pipeline,
            kokoro_voice=kokoro_voice,
            on_turn_start=self._on_turn_start,
            on_text_chunk=self._on_text_chunk,
            on_chunk_ready=self._on_chunk_ready,
            on_audio_chunk=self._on_audio_chunk,
            on_show_slide=self._on_show_slide,
            on_open_sketchpad=self._on_open_sketchpad,
            on_take_photo=self._on_take_photo,
            on_section_advanced=self._on_section_advanced,
            on_curriculum_complete=self._on_curriculum_complete,
            on_turn_complete=self._on_turn_complete,
            on_response_end=self._on_response_end,
            on_tts_playing=self._on_tts_playing,
            on_error=self._on_error,
        )

    # ── public API ─────────────────────────────────────────────────────────────

    async def run_intro(self, curriculum: Curriculum, messages: list[dict]) -> None:
        """Run the one-time intro turn in a thread pool."""
        await asyncio.to_thread(self.agent.run_intro, curriculum, messages)

    async def run_turn(
        self,
        curriculum: Curriculum,
        messages: list[dict],
        agent_instructions: str | None,
        lesson_goal: str | None = None,
    ) -> None:
        """Run one full agent turn (may chain tool calls) in a thread pool."""
        await asyncio.to_thread(
            self.agent.run_turn, curriculum, messages, agent_instructions, lesson_goal
        )

    async def decompose_pdf(
        self,
        pdf_path: str,
        on_progress: Callable[[str], None] | None = None,
    ) -> Curriculum:
        """Decompose a PDF into a Curriculum in a thread pool."""
        return await asyncio.to_thread(
            self.agent.decompose_pdf, pdf_path, on_progress
        )

    async def generate_instructions(self, description: str) -> str:
        return await asyncio.to_thread(self.agent.generate_instructions, description)

    def handle_tool_result(self, inv_id: str, result: dict) -> None:
        """
        Called from the async WS receive loop when the client sends a tool_result.
        Unblocks the agent thread that is waiting on done_event.
        """
        entry = self._tool_events.pop(inv_id, None)
        if entry is None:
            return
        done_event, result_holder = entry
        result_holder[0] = result.get("drawing") or result.get("photo")
        done_event.set()

    # ── internal: fire-and-forget WS send from worker thread ──────────────────

    def _fire(self, coro) -> None:
        """Schedule *coro* on the event loop and block until it completes."""
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        fut.result()  # propagates exceptions; blocks thread until send done

    # ── TeachingAgent callbacks ────────────────────────────────────────────────

    def _on_turn_start(self) -> None:
        self._fire(self._send({"event": "turn_start"}))

    def _on_text_chunk(self, text: str) -> None:
        self._fire(self._send({"event": "text_chunk", "text": text}))

    def _on_chunk_ready(self, tag: str, turn_idx: int, chunk_idx: int) -> None:
        # Signals the client that a text segment has been handed to TTS;
        # the matching audio_chunk will arrive shortly.
        self._fire(self._send({
            "event": "chunk_ready",
            "tag": tag,
            "turn_idx": turn_idx,
            "chunk_idx": chunk_idx,
        }))

    def _on_audio_chunk(
        self, audio: np.ndarray, turn_idx: int, chunk_idx: int
    ) -> None:
        # WebSocket frames are limited to ~1 MB.  Split audio into ≤256 KB
        # sub-chunks (65536 float32 samples ≈ 2.7 s at 24 kHz) to stay well
        # within that limit even after base64 expansion (~33% overhead).
        # Each sub-chunk gets a unique effective chunk_idx so the client's
        # replay buffer stores them individually.
        _MAX_SAMPLES = 65536
        sub_idx = 0
        for start in range(0, max(len(audio), 1), _MAX_SAMPLES):
            piece = audio[start : start + _MAX_SAMPLES]
            data = base64.b64encode(piece.tobytes()).decode()
            effective_idx = chunk_idx * 1000 + sub_idx
            self._fire(self._send({
                "event": "audio_chunk",
                "data": data,
                "sample_rate": KOKORO_SAMPLE_RATE,
                "turn_idx": turn_idx,
                "chunk_idx": effective_idx,
            }))
            sub_idx += 1
        self._fire(self._send({
            "event": "chunk_complete",
            "turn_idx": turn_idx,
            "chunk_idx": chunk_idx,
        }))

    def _on_show_slide(self, page_start: int, page_end: int, caption: str) -> None:
        self._fire(self._send({
            "event": "show_slide",
            "page_start": page_start,
            "page_end": page_end,
            "caption": caption,
        }))

    def _on_open_sketchpad(
        self,
        prompt: str,
        result_holder: list,
        done_event: threading.Event,
        text_bg: str | None = None,
        bg_page: int | None = None,
    ) -> None:
        inv_id = str(uuid.uuid4())
        self._tool_events[inv_id] = (done_event, result_holder)

        im_bg: str | None = None
        if bg_page is not None and self._pdf_path:
            try:
                import fitz
                doc = fitz.open(self._pdf_path)
                if 1 <= bg_page <= len(doc):
                    page = doc[bg_page - 1]
                    pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                    im_bg = "data:image/png;base64," + base64.b64encode(
                        pix.tobytes("png")
                    ).decode()
                doc.close()
            except Exception:
                pass  # non-fatal; canvas will just have no image background

        event: dict = {"event": "open_sketchpad", "prompt": prompt, "invocation_id": inv_id}
        if text_bg:
            event["text_bg"] = text_bg
        if im_bg:
            event["im_bg"] = im_bg
        self._fire(self._send(event))
        # run_turn() will call done_event.wait() after this returns.

    def _on_take_photo(
        self,
        prompt: str,
        result_holder: list,
        done_event: threading.Event,
    ) -> None:
        inv_id = str(uuid.uuid4())
        self._tool_events[inv_id] = (done_event, result_holder)
        self._fire(self._send({
            "event": "take_photo",
            "prompt": prompt,
            "invocation_id": inv_id,
        }))

    def _on_section_advanced(self, curriculum: Curriculum) -> None:
        self._fire(self._send({
            "event": "section_advanced",
            "curriculum": {
                "title": curriculum.title,
                "idx": curriculum.idx,
                "total": len(curriculum.sections),
                "section_title": curriculum.current.get("title", ""),
                "progress": f"{curriculum.idx + 1}/{len(curriculum.sections)}",
            },
        }))

    def _on_curriculum_complete(self) -> None:
        self._fire(self._send({"event": "curriculum_complete"}))

    def _on_turn_complete(self, last_audio: np.ndarray | None) -> None:
        # The canonical turn_complete (with turn_id) is sent by ws_session._run_turn
        # after run_turn() returns.  This callback is intentionally a no-op to
        # avoid sending a duplicate event without a turn_id.
        pass

    def _on_response_end(self) -> None:
        self._fire(self._send({"event": "response_end"}))

    def _on_tts_playing(self, playing: bool) -> None:
        self._fire(self._send({"event": "tts_playing", "playing": playing}))

    def _on_error(self, message: str) -> None:
        self._fire(self._send({"event": "error", "message": message}))
