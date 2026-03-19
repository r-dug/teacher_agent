"""
AgentSession — per-WebSocket-session orchestrator.

Wires the agent trio (TeacherAgent, LessonPlannerAgent, SearchAgent) to the
WebSocket transport.  All agent work is synchronous and runs in a thread pool
via asyncio.to_thread(); WS sends are scheduled back to the event loop using
asyncio.run_coroutine_threadsafe().

Sketchpad blocking pattern (see design doc):
  1. Agent thread calls on_open_sketchpad callback.
  2. Callback schedules a WS send (open_sketchpad event) on the event loop.
  3. Callback stores a threading.Event + result_holder keyed by invocation_id.
  4. run_turn() in teacher_agent.py calls done_event.wait() — blocks
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

from ..voice.config import KOKORO_SAMPLE_RATE, DEFAULT_KOKORO_VOICE
from ...app_state import app_state
from .callbacks import TeachingCallbacks
from .config import DEFAULT_LLM_MODEL
from .curriculum import Curriculum
from .planner_agent import LessonPlannerAgent
from .providers.anthropic import AnthropicLLMProvider
from .providers.openai import OpenAILLMProvider
from .search_agent import SearchAgent
from .teacher_agent import TeacherAgent


class AgentSession:
    """
    Per-WebSocket-session orchestrator.

    Holds a TeacherAgent (teaching loop), a LessonPlannerAgent (PDF
    decomposition), and a SearchAgent (web search for planning), and bridges
    their synchronous callbacks to async WebSocket sends.

    Parameters
    ----------
    send : async callable that accepts a dict and sends it as JSON over WS.
    loop : the running asyncio event loop (needed for run_coroutine_threadsafe).
    tts_provider : primary TTS provider adapter.
    fallback_tts_provider : optional fallback provider adapter.
    tts_voice : current voice id.
    llm_model : Claude model identifier.
    """

    def __init__(
        self,
        send: Callable,
        loop: asyncio.AbstractEventLoop,
        tts_provider=None,
        fallback_tts_provider=None,
        tts_voice: str = DEFAULT_KOKORO_VOICE,
        kokoro_pipeline=None,
        kokoro_voice: str = DEFAULT_KOKORO_VOICE,
        llm_model: str = DEFAULT_LLM_MODEL,
        teach_llm_provider: str = "anthropic",
        teach_llm_model: str | None = None,
        decompose_llm_provider: str | None = None,
        decompose_llm_model: str | None = None,
        openai_api_key: str | None = None,
        openai_timeout_seconds: float = 30.0,
        openai_max_retries: int = 1,
        openai_decompose_timeout_seconds: float | None = None,
        openai_decompose_max_retries: int | None = None,
        openai_decompose_max_input_chars: int = 120000,
        pdf_path: str | None = None,
        session_id: str | None = None,
        user_id: str = "",
    ) -> None:
        self._send = send
        self._loop = loop
        self._pdf_path = pdf_path
        self._session_id = session_id
        self._user_id = user_id
        self._ws_closed = threading.Event()
        # Pending interactive tool invocations: inv_id → (done_event, result_holder)
        self._tool_events: dict[str, tuple[threading.Event, list]] = {}

        if tts_provider is None and kokoro_pipeline is not None:
            from .tts import KokoroTTSProvider
            tts_provider = KokoroTTSProvider(
                pipeline=kokoro_pipeline,
                default_voice=kokoro_voice,
            )

        # ── Build teaching LLM provider chain (primary + optional fallback) ──
        _teach_provider = (teach_llm_provider or "anthropic").strip().lower()
        _teach_model = teach_llm_model or llm_model
        _openai_key = (openai_api_key or "").strip()

        from .providers.fallback import FallbackLLMProvider

        _teach_chain: list[tuple[AnthropicLLMProvider | OpenAILLMProvider, str]] = []
        if _teach_provider == "openai" and _openai_key:
            _teach_chain.append((
                OpenAILLMProvider(
                    api_key=_openai_key,
                    timeout_seconds=max(1.0, float(openai_timeout_seconds)),
                    max_retries=max(0, int(openai_max_retries)),
                ),
                _teach_model,
            ))
            # Anthropic as fallback (picks up ANTHROPIC_API_KEY from env)
            _teach_chain.append((AnthropicLLMProvider(), DEFAULT_LLM_MODEL))
        else:
            _teach_chain.append((AnthropicLLMProvider(), _teach_model))
            # OpenAI as fallback if a key is available
            if _openai_key:
                _teach_chain.append((
                    OpenAILLMProvider(
                        api_key=_openai_key,
                        timeout_seconds=max(1.0, float(openai_timeout_seconds)),
                        max_retries=max(0, int(openai_max_retries)),
                    ),
                    "gpt-4o-mini",
                ))

        llm_provider: AnthropicLLMProvider | OpenAILLMProvider | FallbackLLMProvider = (
            _teach_chain[0][0] if len(_teach_chain) == 1
            else FallbackLLMProvider(_teach_chain)
        )

        # ── Build decomposition agents ───────────────────────────────────────
        _decompose_provider = (decompose_llm_provider or _teach_provider or "anthropic").strip().lower()
        _decompose_model = decompose_llm_model or llm_model
        _decompose_timeout = max(
            1.0,
            float(openai_decompose_timeout_seconds if openai_decompose_timeout_seconds is not None else openai_timeout_seconds),
        )
        _decompose_retries = max(
            0,
            int(openai_decompose_max_retries if openai_decompose_max_retries is not None else openai_max_retries),
        )

        search_agent = SearchAgent(
            model=_decompose_model,
            on_token_usage=self._on_token_usage,
        )

        self._planner = LessonPlannerAgent(
            decompose_llm_provider=_decompose_provider,
            openai_api_key=_openai_key or None,
            openai_timeout_seconds=_decompose_timeout,
            openai_max_retries=_decompose_retries,
            openai_max_input_chars=max(1000, int(openai_decompose_max_input_chars)),
            model=_decompose_model,
            search_agent=search_agent,
            on_token_usage=self._on_token_usage,
        )

        # ── Build callbacks ──────────────────────────────────────────────────
        callbacks = TeachingCallbacks(
            on_turn_start=self._on_turn_start,
            on_text_chunk=self._on_text_chunk,
            on_chunk_ready=self._on_chunk_ready,
            on_audio_chunk=self._on_audio_chunk,
            on_show_slide=self._on_show_slide,
            on_open_sketchpad=self._on_open_sketchpad,
            on_take_photo=self._on_take_photo,
            on_record_video=self._on_record_video,
            on_open_code_editor=self._on_open_code_editor,
            on_open_html_editor=self._on_open_html_editor,
            on_start_timer=self._on_start_timer,
            on_token_usage=self._on_token_usage,
            on_section_advanced=self._on_section_advanced,
            on_curriculum_complete=self._on_curriculum_complete,
            on_turn_complete=self._on_turn_complete,
            on_response_end=self._on_response_end,
            on_tts_playing=self._on_tts_playing,
            on_tts_done=self._on_tts_done,
            on_error=self._on_error,
        )

        # ── Assemble TeacherAgent ────────────────────────────────────────────
        tts_providers = [p for p in [tts_provider, fallback_tts_provider] if p is not None]
        self._teacher = TeacherAgent(
            llm_provider=llm_provider,
            callbacks=callbacks,
            tts_providers=tts_providers,
            tts_voice=tts_voice,
            model=_teach_model,
        )

    # ── public API ─────────────────────────────────────────────────────────────

    async def run_intro(self, curriculum: Curriculum, messages: list[dict], raw_text: str | None = None) -> str | None:
        """Run the first intro turn in a thread pool. Returns captured goal or None."""
        return await asyncio.to_thread(self._teacher.run_intro_turn, curriculum, messages, raw_text)

    async def run_intro_turn(self, curriculum: Curriculum, messages: list[dict], raw_text: str | None = None) -> str | None:
        """Run one intro turn in a thread pool. Returns captured goal or None."""
        async with asyncio.timeout(120):
            return await asyncio.to_thread(self._teacher.run_intro_turn, curriculum, messages, raw_text)

    async def run_turn(
        self,
        curriculum: Curriculum,
        messages: list[dict],
        agent_instructions: str | None,
        lesson_goal: str | None = None,
    ) -> None:
        """Run one full agent turn (may chain tool calls) in a thread pool."""
        async with asyncio.timeout(120):  # 2 minutes per turn
            await asyncio.to_thread(
                self._teacher.run_turn, curriculum, messages, agent_instructions, lesson_goal
            )

    async def decompose_pdf(
        self,
        pdf_path: str,
        on_progress: Callable[[str], None] | None = None,
        student_goal: str | None = None,
    ) -> Curriculum:
        """Decompose a PDF into a Curriculum in a thread pool."""
        cancel_event = threading.Event()
        try:
            async with asyncio.timeout(600):  # 10 minutes; parallel segments are much faster
                return await asyncio.to_thread(
                    self._planner.decompose, pdf_path, on_progress, student_goal, cancel_event
                )
        except TimeoutError:
            cancel_event.set()
            raise

    async def generate_instructions(self, description: str) -> str:
        return await asyncio.to_thread(self._teacher.generate_instructions, description)

    def handle_tool_result(self, inv_id: str, result: dict) -> None:
        """
        Called from the async WS receive loop when the client sends a tool_result.
        Unblocks the agent thread that is waiting on done_event.
        """
        entry = self._tool_events.pop(inv_id, None)
        if entry is None:
            return
        done_event, result_holder = entry
        if "code" in result:
            value = result
        elif "html" in result or "css" in result:
            value = result
        elif "timed_out" in result:
            value = result
        else:
            value = result.get("drawing") or result.get("photo") or result.get("video_frames")
        result_holder.append(value)
        done_event.set()

    # ── internal: fire-and-forget WS send from worker thread ──────────────────

    def _fire(self, coro) -> None:
        """Schedule *coro* on the event loop and block until it completes."""
        if self._ws_closed.is_set():
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            return
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            fut.result()  # propagates exceptions; blocks thread until send done
        except Exception as exc:
            if self._is_ws_send_after_close(exc):
                self._ws_closed.set()
                return
            raise

    @staticmethod
    def _is_ws_send_after_close(exc: Exception) -> bool:
        """Best-effort detection for benign websocket send-after-close races."""
        msg = str(exc)
        patterns = (
            "Unexpected ASGI message 'websocket.send'",
            "after sending 'websocket.close'",
            "response already completed",
            "Cannot call \"send\" once a close message has been sent",
            "WebSocket is not connected",
        )
        if any(p in msg for p in patterns):
            return True
        cause = getattr(exc, "__cause__", None)
        if isinstance(cause, Exception):
            return AgentSession._is_ws_send_after_close(cause)
        context = getattr(exc, "__context__", None)
        if isinstance(context, Exception):
            return AgentSession._is_ws_send_after_close(context)
        return False

    def close(self) -> None:
        """Mark WS transport as closed so callback sends become no-ops."""
        self._ws_closed.set()

    # ── TeacherAgent callbacks ─────────────────────────────────────────────────

    def _on_turn_start(self) -> None:
        self._fire(self._send({"event": "turn_start"}))

    def _on_text_chunk(self, text: str) -> None:
        self._fire(self._send({"event": "text_chunk", "text": text}))

    def _on_chunk_ready(self, tag: str, turn_idx: int, chunk_idx: int) -> None:
        self._fire(self._send({
            "event": "chunk_ready",
            "tag": tag,
            "turn_idx": turn_idx,
            "chunk_idx": chunk_idx,
        }))

    def _on_audio_chunk(
        self, audio: np.ndarray, turn_idx: int, chunk_idx: int
    ) -> None:
        if audio.size == 0:
            self._fire(self._send({
                "event": "chunk_complete",
                "turn_idx": turn_idx,
                "chunk_idx": chunk_idx,
            }))
            return
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
                pass

        event: dict = {"event": "open_sketchpad", "prompt": prompt, "invocation_id": inv_id}
        if text_bg:
            event["text_bg"] = text_bg
        if im_bg:
            event["im_bg"] = im_bg
        self._fire(self._send(event))

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

    def _on_record_video(
        self,
        prompt: str,
        result_holder: list,
        done_event: threading.Event,
    ) -> None:
        inv_id = str(uuid.uuid4())
        self._tool_events[inv_id] = (done_event, result_holder)
        self._fire(self._send({
            "event": "record_video",
            "prompt": prompt,
            "invocation_id": inv_id,
        }))

    def _on_open_code_editor(
        self,
        prompt: str,
        language: str,
        starter_code: str | None,
        result_holder: list,
        done_event: threading.Event,
    ) -> None:
        inv_id = str(uuid.uuid4())
        self._tool_events[inv_id] = (done_event, result_holder)
        event: dict = {
            "event": "open_code_editor",
            "prompt": prompt,
            "language": language,
            "invocation_id": inv_id,
        }
        if starter_code is not None:
            event["starter_code"] = starter_code
        self._fire(self._send(event))

    def _on_open_html_editor(
        self,
        prompt: str,
        starter_html: str | None,
        starter_css: str | None,
        result_holder: list,
        done_event: threading.Event,
    ) -> None:
        inv_id = str(uuid.uuid4())
        self._tool_events[inv_id] = (done_event, result_holder)
        event: dict = {
            "event": "open_html_editor",
            "prompt": prompt,
            "invocation_id": inv_id,
        }
        if starter_html is not None:
            event["starter_html"] = starter_html
        if starter_css is not None:
            event["starter_css"] = starter_css
        self._fire(self._send(event))

    def _on_start_timer(
        self,
        prompt: str,
        duration_seconds: int,
        result_holder: list,
        done_event: threading.Event,
    ) -> None:
        inv_id = str(uuid.uuid4())
        self._tool_events[inv_id] = (done_event, result_holder)
        self._fire(self._send({
            "event": "start_timer",
            "prompt": prompt,
            "duration_seconds": duration_seconds,
            "invocation_id": inv_id,
        }))

    def _on_token_usage(self, call_type: str, model: str, usage) -> None:
        app_state.token_tracker.record_api(
            call_type, model, usage,
            user_id=self._user_id, session_id=self._session_id,
        )

    def _on_tts_done(
        self,
        voice: str,
        characters: int,
        audio_seconds: float,
        synthesis_ms: int,
        estimated_cost_usd: float = 0.0,
    ) -> None:
        app_state.token_tracker.record_tts(
            tts_voice=voice,
            tts_characters=characters,
            tts_audio_seconds=audio_seconds,
            tts_synthesis_ms=synthesis_ms,
            cost_usd=estimated_cost_usd,
            user_id=self._user_id,
        )

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
