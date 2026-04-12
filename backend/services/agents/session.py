"""
AgentSession — per-WebSocket-session orchestrator.

Wires the teaching agent (TeacherAgent + decompose.py + SearchAgent) to
the WebSocket transport.

Concurrency model (Plan B, Commit 2):
  - ``run_turn`` is async-native and runs on the event loop.
  - The LLM streaming call (the only genuinely blocking SDK call) hops to
    a worker thread via ``asyncio.to_thread`` inside ``_do_single_llm_turn``.
    Streaming callbacks (text/audio chunks) fire from that thread and use
    ``_fire`` to schedule WS sends back on the loop.
  - Interactive tool callbacks are async coroutines.  They create a future
    on the loop, register it in ``_tool_futures``, send the open_* event to
    the client, and ``await`` the future for the student's submission.  No
    threading primitives, no cross-thread blocking.
  - On WS disconnect the run_turn task is cancelled, which propagates
    ``CancelledError`` through every awaited future and unwinds cleanly.
    ``cancel_pending_tools`` also cancels any registered futures explicitly.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

import numpy as np

from ..voice.config import KOKORO_SAMPLE_RATE, DEFAULT_KOKORO_VOICE
from ...app_state import app_state
from .callbacks import AgentCallbacks
from .curriculum import Curriculum
from .decompose import decompose_full_pdf
from .search_agent import SearchAgent
from .teacher_agent import TeacherAgent


class AgentSession:
    """
    Per-WebSocket-session orchestrator.

    Holds a TeacherAgent (teaching loop) and a SearchAgent (web search
    for decomposition), and bridges their synchronous callbacks to async
    WebSocket sends.  PDF decomposition is handled by the module-level
    ``decompose.decompose_full_pdf`` function directly — see
    ``decompose_pdf()`` below.

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
        openai_decompose_max_input_chars: int = 120000,
        pdf_path: str | None = None,
        lesson_id: str | None = None,
        session_id: str | None = None,
        user_id: str = "",
        image_provider=None,
        image_style_prefix: str = "",
        visual_aid_config: dict | None = None,
        enrollment_id: str = "",
        storage_dir: Path | None = None,
        messages: list[dict] | None = None,
    ) -> None:
        self._send = send
        self._loop = loop
        self._pdf_path = pdf_path
        self._lesson_id = lesson_id
        self._session_id = session_id
        self._user_id = user_id
        self._messages: list[dict] = list(messages) if messages else []
        self._ws_closed = threading.Event()
        # Pending interactive tool invocations: inv_id → asyncio.Future.
        # The future is created and registered when the dispatcher fires an
        # interactive tool callback; resolved by handle_tool_result when the
        # client posts a tool_result; cancelled by cancel_pending_tools on
        # disconnect.  No threading primitives — run_turn is async-native and
        # awaits the future on the event loop.  See Plan B (rev 2).
        self._tool_futures: dict[str, asyncio.Future] = {}
        # The last open_* / start_timer event sent to the client, cleared on submit.
        # Used to re-send the tool UI after a WS reconnect.
        self._pending_tool_event: dict | None = None
        # Plan B (decision 5): the agent's dispatcher calls
        # on_dispatch_context(tool_use_id, turn_id) before each interactive
        # tool dispatch.  We stash the values here so _await_interactive can
        # persist them with the pending tool row, enabling resume across
        # reconnect / device switch.
        self._dispatch_tool_use_id: str = ""
        self._dispatch_turn_id: str = ""

        # Image generation
        self._image_provider = image_provider
        self._image_style_prefix = image_style_prefix
        self._enrollment_id = enrollment_id
        self._storage_dir = storage_dir or Path("./storage")
        self._current_section_idx: int = 0  # updated by _on_section_advanced
        self._visual_aid_config: dict = visual_aid_config or {}

        if tts_provider is None and kokoro_pipeline is not None:
            from .tts import KokoroTTSProvider
            tts_provider = KokoroTTSProvider(
                pipeline=kokoro_pipeline,
                default_voice=kokoro_voice,
            )

        # ── Build LLM provider chains (Plan B follow-up A) ────────────────
        # Single source of truth: model_chains.py defines TEACH_CHAIN /
        # DECOMPOSE_CHAIN / etc.  build_chain() handles fallback wiring,
        # API key lookup from env vars, and source-specific construction.
        from .model_chains import DECOMPOSE_CHAIN, TEACH_CHAIN
        from .model_config import build_chain

        llm_provider = build_chain(TEACH_CHAIN)
        # Plan B follow-up B3: decomposition lives in decompose.py now.
        # We store the provider + max_input_chars + search agent as
        # per-session state and call decompose_full_pdf() in decompose_pdf().
        self._decompose_provider = build_chain(DECOMPOSE_CHAIN)
        self._decompose_max_input_chars = max(1000, int(openai_decompose_max_input_chars))
        self._search_agent = SearchAgent(on_token_usage=self._on_token_usage)

        # ── Build callbacks ──────────────────────────────────────────────────
        callbacks = AgentCallbacks(
            on_turn_start=self._on_turn_start,
            on_text_chunk=self._on_text_chunk,
            on_chunk_ready=self._on_chunk_ready,
            on_audio_chunk=self._on_audio_chunk,
            # Plan B Commit 4: unified interactive-tool callback.  Per-tool
            # event construction lives in tool_registry.py.
            on_open_interactive_tool=self._on_open_interactive_tool,
            on_search_content=self._on_search_content if self._lesson_id else None,
            on_show_progress=self._on_show_progress,
            on_play_audio_clip=self._on_play_audio_clip,
            # Server-side image work — runs on the loop, not in the client.
            on_generate_visual_aid=self._on_generate_visual_aid,
            on_search_image=self._on_search_image,
            # Capability flags — gate which tools the LLM is offered.
            image_gen_enabled=bool(
                image_provider
                and (visual_aid_config or {}).get("generated_images", {}).get("enabled")
            ),
            image_search_enabled=bool(
                (visual_aid_config or {}).get("web_images", {}).get("enabled")
            ),
            on_dispatch_context=self._on_dispatch_context,
            on_token_usage=self._on_token_usage,
            on_task_complete=self._on_task_complete,
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
        from ...config import settings as _settings
        # Cache Plan C2: stable per-enrollment cache key for the OpenAI
        # Responses API ``prompt_cache_key``.  One key per enrollment so
        # two students on the same lesson don't clobber each other's
        # cache routing.  When enrollment_id is unset (anonymous/test
        # paths) we fall back to ``None`` and the provider just uses
        # the prefix hash alone.
        _cache_key = f"enrollment-{enrollment_id}" if enrollment_id else None
        self._teacher = TeacherAgent(
            llm_provider=llm_provider,
            callbacks=callbacks,
            tts_providers=tts_providers,
            tts_voice=tts_voice,
            model=llm_provider.model,
            memory_strategy=_settings.MEMORY_STRATEGY,
            cache_key=_cache_key,
        )

    def set_distillation_logger(self, logger) -> None:
        """Attach a live turn logger for distillation data collection."""
        self._teacher._callbacks.on_turn_logged = logger

    # ── public API ─────────────────────────────────────────────────────────────

    @property
    def messages(self) -> list[dict]:
        """The live teaching message history owned by this session."""
        return self._messages

    @property
    def image_gen_available(self) -> bool:
        """True when the server has a working image provider configured."""
        return self._image_provider is not None

    def set_tts_voice(self, voice: str) -> None:
        """Update the TTS voice used by the teacher agent."""
        self._teacher.set_tts_voice(voice)

    def set_image_gen_enabled(self, enabled: bool) -> None:
        """Toggle the image-generation capability flag at runtime.

        When enabled=True the ``generate_visual_aid`` tool is added to the
        agent's effective tool list (the LLM is told it can call it).  When
        False the tool is removed and the agent won't offer it.  Safe to
        call between turns.

        Plan B Commit 4: gating moved from per-callback wiring to a flag
        on AgentCallbacks because all interactive tools share one callback now.
        """
        if not self.image_gen_available:
            return  # no provider — always off
        self._teacher._callbacks.image_gen_enabled = bool(enabled)

    async def run_intro(self, curriculum: Curriculum, messages: list[dict], raw_text: str | None = None) -> str | None:
        """Run the first intro turn (async-native).  Returns captured goal or None."""
        return await self._teacher.run_intro_turn(curriculum, messages, raw_text)

    async def run_intro_turn(self, curriculum: Curriculum, messages: list[dict], raw_text: str | None = None) -> str | None:
        """Run one intro turn (async-native).  Returns captured goal or None.

        Plan B: no session-level timeout.  The LLM call inside run_intro_turn
        is wrapped individually so a hung provider doesn't strand the loop.
        """
        return await self._teacher.run_intro_turn(curriculum, messages, raw_text)

    async def run_turn(
        self,
        curriculum: Curriculum,
        agent_instructions: str | None,
        lesson_goal: str | None = None,
        turn_id: str = "",
    ) -> None:
        """Run one full agent turn (may chain tool calls).

        Plan B: ``run_turn`` is async-native and runs entirely on the event
        loop except for the LLM streaming call (which still hops to a thread
        because the SDK is sync).  No session-level wall-clock timeout —
        interactive tools may legitimately suspend for hours while the
        student takes their time.  Per-LLM-call timeout is enforced inside
        the teacher.

        ``turn_id`` is the WS-layer turn identifier; the agent passes it
        through to the persisted pending_tool row so resume across
        reconnect / device switch can correlate the original turn.
        """
        await self._teacher.run_turn(
            curriculum, self._messages, agent_instructions, lesson_goal, turn_id=turn_id,
        )

    # ── Resume path (Plan B Commit 4) ──────────────────────────────────────

    async def resume_pending_tool(
        self,
        invocation_id: str,
        tool_use_id: str,
        tool_name: str,
        turn_id: str,
    ) -> None:
        """Wait for the student to submit (or dismiss) a previously
        persisted interactive tool, then append the resulting tool_result
        block to the message history.

        Plan B Commit 4: this is the entry point ws_session uses on
        reconnect after detecting a ``pending_tool_invocations`` row.  The
        ws_session has already re-sent the open_* event to the client and
        is waiting for the new submission to arrive via the normal
        ``handle_tool_result`` path.  We register the future *here* so the
        future exists when the student posts back.

        Caller is responsible for invoking ``run_turn`` afterwards (with
        the same turn_id) so the agent can process the tool_result and
        continue the conversation.

        On disconnect, the surrounding task is cancelled and the persisted
        row stays in PG (uncleared) so a future reconnect can resume again.
        """
        # Recreate the in-memory future on this fresh session.  The
        # corresponding pending_tool_invocations row is *already* in PG;
        # we don't write it again.
        future: asyncio.Future = self._loop.create_future()
        self._tool_futures[invocation_id] = future
        # _pending_tool_event is set by ws_session when it loads the row;
        # don't overwrite it here.
        self._dispatch_turn_id = turn_id  # for any subsequent dispatches

        try:
            raw = await future
        finally:
            self._tool_futures.pop(invocation_id, None)

        # Find the matching tool_use block in the last assistant message
        # to recover ``tool_input`` (e.g. show_quiz needs ``choices``).
        tool_input = self._find_tool_input(tool_use_id) or {}

        # Build the tool_result via the registry's build_result lambda so
        # the resume path produces the same content as the live dispatch.
        block = TeacherAgent.build_resumed_tool_result(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            tool_input=tool_input,
            raw_result=raw,
        )
        self._messages.append({"role": "user", "content": [block]})

        # Clear the persisted row now that we've consumed the result.
        await self._clear_pending_tool_row()
        self._pending_tool_event = None

    def _find_tool_input(self, tool_use_id: str) -> dict | None:
        """Walk back through messages to find the tool_use block whose id
        matches *tool_use_id* and return its ``input`` dict.  Used by the
        resume path to recover original args (choices, answers, etc.)."""
        for msg in reversed(self._messages):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("id") == tool_use_id
                ):
                    return block.get("input") or {}
        return None

    async def decompose_pdf(
        self,
        pdf_path: str,
        on_progress: Callable[[str], None] | None = None,
        student_goal: str | None = None,
    ) -> Curriculum:
        """Decompose a PDF into a Curriculum in a thread pool.

        Plan B follow-up B3: delegates to ``decompose.decompose_full_pdf``
        (the canonical entry point shared with course_authoring's
        background job path).
        """
        cancel_event = threading.Event()
        try:
            async with asyncio.timeout(600):  # 10 minutes; parallel segments are much faster
                return await asyncio.to_thread(
                    decompose_full_pdf,
                    pdf_path=pdf_path,
                    llm_provider=self._decompose_provider,
                    search_agent=self._search_agent,
                    student_goal=student_goal,
                    on_progress=on_progress,
                    cancel_event=cancel_event,
                    max_input_chars=self._decompose_max_input_chars,
                    on_token_usage=self._on_token_usage,
                )
        except TimeoutError:
            cancel_event.set()
            raise

    async def generate_instructions(self, description: str) -> str:
        return await asyncio.to_thread(self._teacher.generate_instructions, description)

    @property
    def pending_tool_event(self) -> dict | None:
        """The WS event payload for the currently open interactive tool, or None."""
        return self._pending_tool_event

    def cancel_pending_tools(self) -> None:
        """Cancel any in-flight interactive-tool futures.

        Called on WS disconnect or explicit reconnect-cancel.  Each cancel
        propagates ``CancelledError`` through the corresponding ``await`` in
        ``run_turn``, which then unwinds cleanly — no leaked threads, no
        leaked futures.
        """
        for future in list(self._tool_futures.values()):
            if not future.done():
                future.cancel()
        self._tool_futures.clear()
        self._pending_tool_event = None

    def handle_tool_result(self, inv_id: str, result: dict) -> None:
        """
        Called from the WS receive loop (event loop side) when the client
        sends a tool_result.  Resolves the matching future, which awakens
        the awaiter inside ``run_turn``.

        We're already on the event loop here, so we can call
        ``future.set_result`` directly — no ``call_soon_threadsafe`` needed.
        """
        self._pending_tool_event = None
        future = self._tool_futures.pop(inv_id, None)
        if future is None or future.done():
            return
        future.set_result(result)

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
        # Cache Plan C3 + pricing consolidation: emit one summary log line
        # so we can tell at a glance whether prefix caching is paying off
        # for this session — both in raw hit rate and in actual dollars.
        try:
            stats = self._teacher.cache_stats()
        except Exception:  # noqa: BLE001 — telemetry must never break teardown
            return
        if stats["turns"] > 0:
            log.info(
                "[llm-session-summary] enrollment=%s turns=%d tokens_in=%d "
                "tokens_cached=%d hit_rate=%.2f spent=$%.4f saved=$%.4f",
                self._enrollment_id or "-",
                stats["turns"],
                stats["tokens_in"],
                stats["tokens_cached"],
                stats["hit_rate"],
                stats["dollars_spent"],
                stats["dollars_saved"],
            )

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

    def _on_dispatch_context(self, tool_use_id: str, turn_id: str) -> None:
        """Plan B: stash the current dispatch context so the next call to
        ``_await_interactive`` can persist the pending tool row with the
        right tool_use_id and turn_id.  Cleared inside _await_interactive's
        finally block."""
        self._dispatch_tool_use_id = tool_use_id
        self._dispatch_turn_id = turn_id

    async def _on_open_interactive_tool(self, event: dict) -> dict | None:
        """Plan B Commit 4: unified interactive-tool entry point.

        The teacher's tool registry calls this with a fully-built event
        dict (containing ``invocation_id``, the WS event name, and the
        tool-specific payload).  We just delegate to ``_await_interactive``
        which handles future registration, persistence, send, and await.
        """
        return await self._await_interactive(event)

    async def _await_interactive(self, event: dict) -> dict | None:
        """Generic helper: register a future for ``event['invocation_id']``,
        persist the pending tool row to PG (so a different session/device
        can resume), send the open_* event to the client, await the
        student's submission.

        Returns the raw client result dict, or None if cancelled / dismissed.
        On WS death the surrounding task is cancelled, which propagates
        through this await as ``CancelledError`` and is re-raised so the
        agent's run_turn unwinds cleanly.  The persisted PG row is cleared
        in either case (resolution or cancellation) inside the finally block.
        """
        inv_id = event["invocation_id"]
        future: asyncio.Future = self._loop.create_future()
        self._tool_futures[inv_id] = future
        self._pending_tool_event = event

        # Snapshot + clear the dispatch context the agent gave us.
        tool_use_id = self._dispatch_tool_use_id
        turn_id = self._dispatch_turn_id
        self._dispatch_tool_use_id = ""
        self._dispatch_turn_id = ""

        # Persist to PG so a reconnecting WS / different device can resume.
        # Best-effort: failures are logged but don't break the dispatch.
        if self._enrollment_id and tool_use_id:
            try:
                from ..db import models as _models, connection as _db
                async with _db.acquire() as conn:
                    await _models.put_pending_tool(
                        conn,
                        enrollment_id=self._enrollment_id,
                        invocation_id=inv_id,
                        tool_name=event.get("event", "unknown"),
                        tool_use_id=tool_use_id,
                        turn_id=turn_id,
                        event_payload=event,
                    )
            except Exception:
                log.exception("[AgentSession] failed to persist pending tool")

        try:
            await self._send(event)
        except Exception:
            # Send failed (WS already closed?) — clean up before re-raising.
            self._tool_futures.pop(inv_id, None)
            self._pending_tool_event = None
            await self._clear_pending_tool_row()
            raise
        try:
            return await future
        finally:
            # Whether resolved, cancelled, or errored: drop our tracking.
            self._tool_futures.pop(inv_id, None)
            self._pending_tool_event = None
            await self._clear_pending_tool_row()

    async def _clear_pending_tool_row(self) -> None:
        """Best-effort delete of this enrollment's pending_tool row.  Called
        from _await_interactive's finally block; safe to call when no row
        exists.  Failures are logged but never raised."""
        if not self._enrollment_id:
            return
        try:
            from ..db import models as _models, connection as _db
            async with _db.acquire() as conn:
                await _models.clear_pending_tool(conn, self._enrollment_id)
        except Exception:
            log.exception("[AgentSession] failed to clear pending tool")

    # ── Server-side image work ────────────────────────────────────────────
    # Plan B Commit 4: the 11 client-interactive _on_* methods (sketchpad,
    # photo, video, code editor, html editor, timer, text input, quiz,
    # fill-in-the-blank, flashcards, ordering) are gone — they all route
    # through ``_on_open_interactive_tool`` now.  Only generate_visual_aid
    # and search_image survive as dedicated methods because they do real
    # server-side work (image generation, image search) instead of waiting
    # on a client tool_result.
    async def _on_generate_visual_aid(
        self,
        prompt: str,
        caption: str,
        tool_use_id: str,
    ) -> dict | None:
        """Generate an image, persist it, send the show_image event.

        Plan B: this is now a plain async coroutine that runs on the event
        loop directly.  Returns ``{"image_url": str}`` on success or ``None``
        on failure.  The agent's run_turn awaits it.
        """
        try:
            await self._send({"event": "generating_image", "caption": caption})

            provider = self._image_provider
            if provider is None:
                await self._send({"event": "generation_failed", "reason": "Image generation is not configured."})
                return None

            styled_prompt = (self._image_style_prefix + prompt).strip()

            try:
                image = await asyncio.to_thread(provider.generate, styled_prompt)
            except Exception as exc:
                log.exception("[AgentSession] image generation failed")
                reason = str(exc) or type(exc).__name__
                await self._send({"event": "generation_failed", "reason": reason})
                return None

            # Persist the image under storage/enrollment_assets/<enrollment_id>/<asset_id>.png
            try:
                from ...db import models, connection as _db

                asset_dir = self._storage_dir / "enrollment_assets" / self._enrollment_id
                asset_dir.mkdir(parents=True, exist_ok=True)

                asset_id = str(uuid.uuid4())
                image_path = asset_dir / f"{asset_id}.png"
                image_path.write_bytes(image.image_bytes)

                rel_path = str(image_path.relative_to(self._storage_dir))

                async with _db.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT COUNT(*) FROM enrollment_assets WHERE enrollment_id = $1",
                        self._enrollment_id,
                    )
                    idx = row[0] if row else 0

                    await models.create_enrollment_asset(
                        conn,
                        enrollment_id=self._enrollment_id,
                        section_idx=self._current_section_idx,
                        image_path=rel_path,
                        prompt=prompt,
                        tool_use_id=tool_use_id,
                        revised_prompt=image.revised_prompt,
                        idx=idx,
                    )

                image_url = f"/api/lessons/assets/{rel_path}"
                log.info("[AgentSession] image saved: %s", rel_path)

                await self._send({
                    "event": "show_image",
                    "image_url": image_url,
                    "caption": caption,
                    "prompt": prompt,
                })
                return {"image_url": image_url}

            except Exception:
                log.exception("[AgentSession] failed to persist generated image")
                await self._send({"event": "generation_failed", "reason": "Failed to save image."})
                return None

        except Exception:
            log.exception("[AgentSession] unexpected error in _on_generate_visual_aid")
            return None

    async def _on_search_image(
        self,
        query: str,
        caption: str,
        tool_use_id: str,
    ) -> dict | None:
        """Search for an image and send the show_image event.

        Plan B: plain async coroutine, returns ``{"image_url": str}`` or
        ``None``.  The agent's run_turn awaits it.
        """
        try:
            await self._send({"event": "generating_image", "caption": f"Searching: {caption}"})

            from ..images.search import search_image_sync
            from ...config import settings

            api_key = (settings.OPENAI_API_KEY or "").strip()
            if not api_key:
                await self._send({"event": "generation_failed", "reason": "API key not configured."})
                return None

            image_url = await asyncio.to_thread(search_image_sync, query, api_key)
            if not image_url:
                await self._send({"event": "generation_failed", "reason": "No image found."})
                return None

            await self._send({
                "event": "show_image",
                "image_url": image_url,
                "caption": caption,
                "prompt": query,
            })
            return {"image_url": image_url}

        except Exception:
            log.exception("[AgentSession] unexpected error in _on_search_image")
            try:
                await self._send({"event": "generation_failed", "reason": "Image search failed."})
            except Exception:
                pass  # WS may already be closed
            return None

    # ── Non-blocking display tools ───────────────────────────────────────────
    # These fire from the (async) run_turn dispatcher.  They schedule WS sends
    # via create_task instead of _fire because we're already on the event loop.

    def _on_show_progress(self, curriculum) -> None:
        sections_data = []
        for i, sec in enumerate(curriculum.sections):
            sections_data.append({
                "title": sec.get("title", f"Section {i + 1}"),
                "idx": i,
                "completed": i < curriculum.idx,
            })
        asyncio.create_task(self._send({
            "event": "show_progress",
            "sections": sections_data,
            "current_idx": curriculum.idx,
            "tasks": curriculum.current_tasks(),
        }))

    def _on_play_audio_clip(self, text: str, speed: float) -> None:
        asyncio.create_task(self._send({
            "event": "play_audio_clip",
            "text": text,
            "speed": speed,
        }))

    # ── RAG ────────────────────────────────────────────────────────────────────

    async def _on_search_content(self, query: str, current_idx: int) -> str:
        """Async RAG callback — runs the DB query directly on the event loop."""
        try:
            from ...db import connection as _db, models
            async with _db.acquire() as conn:
                results = await models.search_sections(
                    conn, self._lesson_id, query, exclude_idx=current_idx, limit=3,
                )
            if not results:
                return "No matching content found in other sections."
            parts = []
            for r in results:
                parts.append(f"[Section {r['idx'] + 1}: {r['title']}]\n{r['excerpt']}")
            return "\n\n".join(parts)
        except Exception as exc:
            log.warning("search_content failed: %s", exc)
            return "Search failed — continue teaching with available context."

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

    def _on_task_complete(self, curriculum: Curriculum) -> None:
        # Fired from the (async) run_turn dispatcher — schedule the WS send
        # via create_task instead of _fire (we're already on the event loop).
        asyncio.create_task(self._send({
            "event": "task_progress",
            "section_idx": curriculum.idx,
            "tasks": curriculum.current_tasks(),
            "all_done": curriculum.all_tasks_done(),
        }))

    def _on_section_advanced(self, curriculum: Curriculum) -> None:
        old_idx = self._current_section_idx
        self._current_section_idx = curriculum.idx
        asyncio.create_task(self._send({
            "event": "section_advanced",
            "curriculum": {
                "title": curriculum.title,
                "idx": curriculum.idx,
                "total": len(curriculum.sections),
                "section_title": curriculum.current.get("title", ""),
                "progress": f"{curriculum.idx + 1}/{len(curriculum.sections)}",
            },
            "tasks": curriculum.current_tasks(),
        }))
        # Persist and award points immediately so the celebration fires now,
        # not after the whole turn finishes.
        asyncio.create_task(self._persist_and_award(
            curriculum.idx, curriculum.task_progress_json(),
            old_idx=old_idx, is_complete=False,
        ))

    async def _persist_and_award(
        self, idx: int, task_progress_json: str,
        old_idx: int = 0, is_complete: bool = False,
    ) -> None:
        """Persist section index and award points immediately (called mid-turn)."""
        try:
            from ..db import models, connection as _db
            from ..db.connection import ANON_USER_ID
            from .points import award_section_advance, award_lesson_complete, award_daily_points_if_needed

            async with _db.acquire() as conn:
                await models.update_enrollment(
                    conn, self._enrollment_id,
                    current_section_idx=idx,
                    task_progress=task_progress_json,
                    completed=int(is_complete),
                )

                if self._user_id and self._user_id != ANON_USER_ID:
                    points_earned = 0
                    reason = ""
                    if idx > old_idx:
                        pts = await award_section_advance(conn, self._user_id, self._enrollment_id, old_idx, idx)
                        points_earned += pts
                        reason = "section_advance"
                    if is_complete:
                        pts = await award_lesson_complete(conn, self._user_id, self._enrollment_id)
                        points_earned += pts
                        reason = "lesson_complete"
                    daily_pts = await award_daily_points_if_needed(conn, self._user_id)
                    points_earned += daily_pts

                    if points_earned > 0:
                        user_points = await models.get_user_points(conn, self._user_id)
                        total = int(user_points["total_points"]) if user_points else points_earned
                        await self._send({
                            "event": "points_awarded",
                            "points": points_earned,
                            "total": total,
                            "reason": reason or "daily",
                        })
        except Exception:
            log.exception("failed to persist section advance / award points")

    def _on_curriculum_complete(self) -> None:
        # Fired from the (async) run_turn dispatcher — use create_task.
        asyncio.create_task(self._send({"event": "curriculum_complete"}))
        asyncio.create_task(self._persist_and_award(
            self._current_section_idx, "{}",
            old_idx=self._current_section_idx, is_complete=True,
        ))

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
