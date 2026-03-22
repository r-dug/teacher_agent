"""
WebSocket session handler for the backend.

One WebSocket connection per teaching session.  The handler runs two concurrent
loops:
  - receive_loop: processes inbound messages from the frontend server
  - send_loop: forwards events from the per-session asyncio.Queue to the client

The TeacherAgent runs inside asyncio.to_thread() so it never blocks the event
loop.  Agent callbacks schedule WS sends back onto the event loop via
asyncio.run_coroutine_threadsafe() (see AgentSession in services/agents/session.py).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import quote

log = logging.getLogger(__name__)

import aiosqlite
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect as ws_connect

from ..app_state import app_state, registry
from ..config import settings
from ..db import connection as db, models
from ..services.agents.session import AgentSession
from ..services.voice.realtime import (
    float32_b64_to_pcm16_b64,
    pcm16_b64_to_float32,
    run_realtime_voice_turn,
    usage_from_realtime,
)
from ..services.voice.config import OPENAI_STT_MODELS
from ..services.voice.stt import (
    select_stt_provider,
    transcribe,
    transcribe_file,
    transcribe_file_openai,
    transcribe_openai,
    load_stt_model,
)
from ..services.agents.curriculum import Curriculum

router = APIRouter(tags=["ws"])


# ── dependency shorthand ───────────────────────────────────────────────────────

Conn = Annotated[aiosqlite.Connection, Depends(db.get)]


def _normalize_voice_arch(value: str | None) -> str | None:
    arch = (value or "").strip().lower()
    if arch in {"chained", "realtime"}:
        return arch
    return None


# ── per-session state ──────────────────────────────────────────────────────────

class SessionState:
    """Mutable state for one active teaching session."""

    def __init__(
        self,
        session_id: str,
        lesson_id: str,
        enrollment_id: str,
        curriculum: Curriculum,
        agent_instructions: str | None,
        agent_session: AgentSession,
        pdf_path: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.lesson_id = lesson_id
        self.enrollment_id = enrollment_id
        self.curriculum = curriculum
        self.agent_instructions = agent_instructions
        self.agent_session = agent_session
        self.pdf_path = pdf_path
        self.agent_task: asyncio.Task | None = None
        self.last_turn_id: str | None = None
        self.turn_status: str = "idle"  # 'idle' | 'running' | 'complete' | 'failed'
        self.stt_language: str | None = None  # None = auto-detect
        self.stt_model_size: str | None = None  # None = use app default
        self.stt_provider: str = settings.effective_stt_provider()  # local | openai
        self.voice_arch: str = settings.effective_voice_arch()  # chained | realtime
        self.realtime_turn_idx: int = 0
        self.realtime_ws = None
        self.realtime_reader_task: asyncio.Task | None = None
        self.realtime_send_lock: asyncio.Lock = asyncio.Lock()
        self.realtime_stream_connected: bool = False
        self.realtime_streaming: bool = False
        self.realtime_stream_turn: RealtimeStreamTurn | None = None
        self.code_run_semaphore: asyncio.Semaphore = asyncio.Semaphore(3)
        self.phase: str = "intro"  # 'intro' | 'teaching'
        self.lesson_goal: str | None = None
        # Multi-turn intro messages (separate from teaching messages).
        self.intro_messages: list[dict] = []
        self.intro_raw_text: str | None = None
        # Closures injected by ws_session() for deferred decomposition flow.
        self.deferred_decompose_fn = None
        self.first_teaching_task_fn = None
        self.handle_intro_turn_fn = None

    @property
    def messages(self) -> list[dict]:
        return self.agent_session.messages


@dataclass(slots=True)
class RealtimeStreamTurn:
    turn_id: str
    turn_idx: int
    user_text: str = ""
    assistant_text: str = ""
    chunk_idx: int = 0
    transcript_sent: bool = False
    turn_start_sent: bool = False
    tts_playing_sent: bool = False


# ── WebSocket endpoint ─────────────────────────────────────────────────────────

@router.websocket("/ws/{session_id}")
async def ws_session(
    websocket: WebSocket,
    session_id: str,
    lesson_id: str,
    conn: Conn,
):
    """
    Main WebSocket endpoint for a teaching session.

    Query parameters
    ----------------
    session_id : path parameter — opaque session token.
    lesson_id  : query parameter — which lesson to load.
    """
    await websocket.accept()

    # Validate session
    session = await models.get_session(conn, session_id)
    if session is None:
        await websocket.send_json({"event": "error", "message": "Invalid session"})
        await websocket.close(code=4001)
        return

    await models.touch_session(conn, session_id)

    # Announce server capabilities so the client can show/lock toggles accordingly.
    await websocket.send_json({
        "event": "capabilities",
        "image_gen_available": app_state.image_provider is not None,
    })

    # Load lesson
    lesson = await models.get_lesson(conn, lesson_id)
    if lesson is None:
        await websocket.send_json({"event": "error", "message": "Lesson not found"})
        await websocket.close(code=4004)
        return

    # Check access: creator or published lesson
    user_id: str = session["user_id"]
    if lesson["creator_id"] != user_id and lesson.get("visibility") != "published":
        await websocket.send_json({"event": "error", "message": "Access denied"})
        await websocket.close(code=4003)
        return

    # Lazy enrollment: get or create per-user state row
    enrollment = await models.get_or_create_enrollment(conn, lesson_id, user_id)
    enrollment_id: str = enrollment["id"]

    sections = await models.get_sections(conn, lesson_id)
    messages = await models.get_messages(conn, enrollment_id)
    curriculum = Curriculum(
        title=lesson["title"],
        sections=[_section_to_dict(s) for s in sections],
        idx=enrollment["current_section_idx"],
    )

    # For existing lessons, send the curriculum to the client immediately so it
    # can render the sidebar and curriculum state without waiting for decompose_complete.
    if sections:
        await websocket.send_json({
            "event": "decompose_complete",
            "lesson_id": lesson_id,
            "curriculum": {
                "title": curriculum.title,
                "sections": curriculum.sections,
                "idx": curriculum.idx,
            },
        })

    # Send prior conversation history so the client can render past turns.
    if messages:
        enrollment_assets = await models.get_enrollment_assets(conn, enrollment_id)
        history_turns = _build_history_turns(messages, enrollment_assets)
        if history_turns:
            await websocket.send_json({"event": "history", "turns": history_turns})

    # Register session in the event registry so background tasks can push events
    event_queue: asyncio.Queue = registry.register(session_id)

    loop = asyncio.get_event_loop()

    async def _send(event: dict) -> None:
        await websocket.send_json(event)

    pdf_full_path = (
        str(settings.STORAGE_DIR / lesson["pdf_path"])
        if lesson.get("pdf_path") else None
    )
    default_tts_voice = (
        getattr(app_state.tts_provider, "default_voice", "") or settings.default_tts_voice()
    )
    agent_session = AgentSession(
        send=_send,
        loop=loop,
        tts_provider=app_state.tts_provider,
        fallback_tts_provider=app_state.tts_fallback_provider,
        tts_voice=default_tts_voice,
        kokoro_pipeline=app_state.kokoro_pipeline,
        kokoro_voice=settings.DEFAULT_VOICE,
        llm_model=settings.LLM_MODEL,
        teach_llm_provider=settings.TEACH_LLM_PROVIDER,
        teach_llm_model=settings.TEACH_LLM_MODEL,
        decompose_llm_provider=settings.effective_decompose_llm_provider(),
        decompose_llm_model=settings.effective_decompose_llm_model(),
        openai_api_key=settings.OPENAI_API_KEY,
        openai_timeout_seconds=settings.OPENAI_LLM_TIMEOUT_S,
        openai_max_retries=settings.OPENAI_LLM_MAX_RETRIES,
        openai_decompose_timeout_seconds=settings.OPENAI_DECOMPOSE_TIMEOUT_S,
        openai_decompose_max_retries=settings.OPENAI_DECOMPOSE_MAX_RETRIES,
        openai_decompose_max_input_chars=settings.OPENAI_DECOMPOSE_MAX_INPUT_CHARS,
        pdf_path=pdf_full_path,
        session_id=session_id,
        user_id=session.get("user_id", ""),
        image_provider=app_state.image_provider,
        image_style_prefix=settings.IMAGE_GEN_STYLE_PREFIX,
        enrollment_id=enrollment_id,
        storage_dir=settings.STORAGE_DIR,
        messages=messages,
    )

    state = SessionState(
        session_id=session_id,
        lesson_id=lesson_id,
        enrollment_id=enrollment_id,
        curriculum=curriculum,
        agent_instructions=None,  # set by client via set_instructions event
        agent_session=agent_session,
        pdf_path=pdf_full_path,
    )
    # Restore persisted lesson_goal (captured during intro, saved before decomposition).
    state.lesson_goal = enrollment.get("lesson_goal") or None
    # Skip intro for resumed lessons (messages exist) or already-decomposed lessons (sections exist).
    if messages or sections:
        state.phase = "teaching"

    async def _auto_start() -> None:
        """Run the first intro turn (overview + goal question) for a fresh lesson.

        If sections are not yet available (deferred decomposition), extract a raw
        text preview from the PDF so the agent can give a meaningful overview.

        The intro is now a multi-turn loop: the agent may ask follow-up questions
        before calling capture_lesson_goal.  Subsequent user messages are routed
        back here via _handle_intro_turn().
        """
        import uuid as _uuid
        turn_id = str(_uuid.uuid4())
        state.last_turn_id = turn_id
        state.turn_status = "running"
        log.info("[auto-start] launching intro turn for lesson %s", lesson_id)

        raw_text: str | None = None
        if not state.curriculum.sections and state.pdf_path:
            try:
                import fitz
                def _extract() -> str:
                    doc = fitz.open(state.pdf_path)
                    pages = [doc[i].get_text() for i in range(min(5, len(doc)))]
                    doc.close()
                    return "\n\n".join(p for p in pages if p.strip())
                raw_text = await asyncio.to_thread(_extract)
            except Exception:
                pass  # non-fatal; intro will use title only

        state.intro_raw_text = raw_text

        try:
            goal = await state.agent_session.run_intro_turn(
                state.curriculum, state.intro_messages, raw_text
            )
            state.turn_status = "complete"
            await websocket.send_json({"event": "turn_complete", "turn_id": turn_id})

            if goal:
                # Agent captured the goal on the very first exchange (rare but valid).
                state.lesson_goal = goal
                state.phase = "teaching"
                await models.update_enrollment(conn, state.enrollment_id, lesson_goal=goal)
                if state.deferred_decompose_fn:
                    asyncio.create_task(state.deferred_decompose_fn())
                elif state.first_teaching_task_fn:
                    state.agent_task = state.first_teaching_task_fn()
            # else: agent asked a follow-up — wait for student's response.

        except asyncio.CancelledError:
            state.turn_status = "failed"
        except Exception as exc:
            log.exception("[auto-start] intro turn raised")
            state.turn_status = "failed"
            await websocket.send_json({"event": "error", "message": str(exc) or type(exc).__name__})

    async def _handle_intro_turn(user_text: str, turn_id: str) -> None:
        """
        Continue the goal-gathering intro loop with the student's response.

        Appends the student's message to the shared intro_messages list, runs
        another intro turn, and either:
          - Captures the goal → transitions to teaching + kicks off decomposition.
          - Gets another follow-up question → sends turn_complete and waits.
        """
        state.intro_messages.append({"role": "user", "content": user_text})
        state.turn_status = "running"

        try:
            goal = await state.agent_session.run_intro_turn(
                state.curriculum, state.intro_messages, state.intro_raw_text
            )
            state.turn_status = "complete"
            await websocket.send_json({"event": "turn_complete", "turn_id": turn_id})

            if goal:
                state.lesson_goal = goal
                state.phase = "teaching"
                await models.update_enrollment(conn, state.enrollment_id, lesson_goal=goal)
                if state.deferred_decompose_fn:
                    asyncio.create_task(state.deferred_decompose_fn())
                elif state.first_teaching_task_fn:
                    state.agent_task = state.first_teaching_task_fn()
            # else: agent asked another follow-up — wait for next student message.

        except asyncio.CancelledError:
            state.turn_status = "failed"
        except Exception as exc:
            log.exception("[intro-turn] raised")
            state.turn_status = "failed"
            await websocket.send_json({"event": "error", "message": str(exc) or type(exc).__name__})

    async def _deferred_decompose() -> None:
        """Run goal-informed decomposition after the intro, then push decompose_complete."""
        # Re-fetch pdf_path in case the WS connected before the upload endpoint committed.
        if not state.pdf_path:
            refreshed = await models.get_lesson(conn, lesson_id)
            if refreshed and refreshed.get("pdf_path"):
                state.pdf_path = str(settings.STORAGE_DIR / refreshed["pdf_path"])
                state.agent_session._pdf_path = state.pdf_path
        if not state.pdf_path:
            log.error("[deferred-decompose] no PDF path on lesson %s — cannot decompose", lesson_id)
            await websocket.send_json({"event": "error", "message": "No PDF found for this lesson. Please re-upload."})
            return
        def on_progress(msg: str) -> None:
            registry.send_threadsafe(session_id, {"event": "status", "message": msg}, loop)
        try:
            await websocket.send_json({"event": "decompose_start"})
            await websocket.send_json({"event": "status", "message": "Analysing your document with your goals in mind…"})
            curriculum = await state.agent_session.decompose_pdf(
                state.pdf_path, on_progress, student_goal=state.lesson_goal
            )
            if not curriculum.sections:
                raise RuntimeError(
                    "Decomposition produced no sections. Try reducing chapter scope or adjusting the objective prompt."
                )
            await models.upsert_sections(conn, lesson_id, curriculum.sections)
            await models.update_lesson(conn, lesson_id, title=curriculum.title)
            await registry.send(session_id, {
                "event": "decompose_complete",
                "lesson_id": lesson_id,
                "curriculum": {
                    "title": curriculum.title,
                    "sections": curriculum.sections,
                    "idx": 0,
                },
            })
        except Exception as exc:
            log.exception("[deferred-decompose] raised")
            await websocket.send_json({"event": "error", "message": f"Decomposition failed: {exc}"})

    async def _first_teaching_turn() -> None:
        """Run the first proper teaching turn after deferred decomposition completes."""
        import uuid as _uuid
        turn_id = str(_uuid.uuid4())
        state.last_turn_id = turn_id
        state.turn_status = "running"
        log.info("[first-teaching] starting first teaching turn for lesson %s", lesson_id)
        try:
            await state.agent_session.run_turn(
                state.curriculum,
                state.agent_instructions,
                lesson_goal=state.lesson_goal,
            )
            state.turn_status = "complete"
            await _save_state(conn, state)
            await websocket.send_json({"event": "turn_complete", "turn_id": turn_id})
        except asyncio.CancelledError:
            state.turn_status = "failed"
        except Exception as exc:
            log.exception("[first-teaching] raised")
            state.turn_status = "failed"
            await websocket.send_json({"event": "error", "message": str(exc) or type(exc).__name__})

    state.deferred_decompose_fn = _deferred_decompose
    state.first_teaching_task_fn = lambda: asyncio.create_task(_first_teaching_turn())
    state.handle_intro_turn_fn = _handle_intro_turn

    try:
        receive_task = asyncio.create_task(
            _receive_loop(websocket, state, conn, loop, _auto_start)
        )
        send_task = asyncio.create_task(
            _send_loop(websocket, event_queue, state, _auto_start)
        )

        # Auto-start is client-driven: the client sends 'start_lesson' after it has
        # dispatched its initial config (set_instructions, set_voice, etc.).
        # For fresh lessons, auto-start also fires from _send_loop after decompose_complete.
        # For lessons that already have sections but no conversation history, kick off
        # the first teaching turn immediately (decompose_complete was sent directly above,
        # not via the queue, so _send_loop won't trigger it).
        if state.phase == "teaching" and not state.messages and state.first_teaching_task_fn:
            state.agent_task = state.first_teaching_task_fn()

        done, pending = await asyncio.wait(
            {receive_task, send_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()

    except WebSocketDisconnect:
        pass
    finally:
        state.agent_session.close()
        await _close_realtime_stream(state)
        registry.unregister(session_id)
        # Cancel any running agent turn
        if state.agent_task and not state.agent_task.done():
            state.agent_task.cancel()
        # Persist lesson state on disconnect
        await _save_state(conn, state)


# ── receive loop ───────────────────────────────────────────────────────────────

async def _receive_loop(
    websocket: WebSocket,
    state: SessionState,
    conn: aiosqlite.Connection,
    loop: asyncio.AbstractEventLoop,
    auto_start=None,
) -> None:
    async for raw in websocket.iter_text():
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await websocket.send_json({"event": "error", "message": "Invalid JSON"})
            continue

        event = msg.get("event")

        if event == "audio_input":
            await _handle_audio_input(websocket, msg, state, conn)

        elif event == "tool_result":
            inv_id = msg.get("invocation_id", "")
            result_payload = msg.get("result", {})
            # Reject oversized tool results (drawings, photos, video frames).
            # Encoded payload must not exceed 50 MB of raw text.
            if len(raw) > 50 * 1024 * 1024:
                await websocket.send_json({
                    "event": "error",
                    "message": "tool_result payload exceeds 50 MB limit",
                })
                continue
            state.agent_session.handle_tool_result(inv_id, result_payload)

        elif event == "set_instructions":
            state.agent_instructions = msg.get("instructions") or None
            if state.realtime_stream_connected:
                try:
                    await _send_realtime_session_update(state)
                except Exception as exc:
                    await websocket.send_json({"event": "error", "message": f"Realtime session update failed: {exc}"})

        elif event == "set_voice":
            voice = msg.get("voice")
            if voice:
                state.agent_session.set_tts_voice(voice)

        elif event == "set_voice_arch":
            requested_arch = _normalize_voice_arch(msg.get("voice_arch"))
            if requested_arch is None:
                await websocket.send_json({
                    "event": "error",
                    "message": "Invalid voice_arch. Use 'chained' or 'realtime'.",
                })
                continue
            state.voice_arch = requested_arch
            if requested_arch != "realtime":
                await _close_realtime_stream(state)
            if state.agent_task and not state.agent_task.done():
                await websocket.send_json({
                    "event": "status",
                    "message": f"Conversation mode will switch to {requested_arch} after this turn.",
                })
            else:
                await websocket.send_json({
                    "event": "status",
                    "message": f"Conversation mode set to {requested_arch}.",
                })

        elif event == "realtime_stream_start":
            await _handle_realtime_stream_start(websocket, state, conn)

        elif event == "realtime_stream_chunk":
            await _handle_realtime_stream_chunk(websocket, msg, state, conn)

        elif event == "realtime_stream_stop":
            await _handle_realtime_stream_stop(websocket, state)

        elif event == "set_stt_language":
            lang = msg.get("language")  # BCP-47 code or None / "" for auto
            state.stt_language = lang or None

        elif event == "set_stt_model":
            size = msg.get("model_size")
            if size:
                state.stt_model_size = size
                if state.stt_provider != "openai" and size not in app_state.stt_models:
                    asyncio.create_task(_load_stt_model_bg(websocket, size))

        elif event == "set_stt_provider":
            provider_raw = msg.get("provider")
            provider = select_stt_provider(provider_raw)
            state.stt_provider = provider
            if provider == "openai":
                if state.stt_model_size not in OPENAI_STT_MODELS:
                    state.stt_model_size = settings.OPENAI_STT_MODEL
            else:
                if state.stt_model_size in OPENAI_STT_MODELS or not state.stt_model_size:
                    state.stt_model_size = settings.STT_MODEL_SIZE
                if state.stt_model_size not in app_state.stt_models:
                    asyncio.create_task(_load_stt_model_bg(websocket, state.stt_model_size))
            await websocket.send_json({"event": "status", "message": f"STT provider set to {provider}"})

        elif event == "set_image_gen":
            enabled = bool(msg.get("enabled", False))
            state.agent_session.set_image_gen_enabled(enabled)

        elif event == "reconnect":
            await _handle_reconnect(websocket, msg, state)

        elif event == "cancel_turn":
            if state.agent_task and not state.agent_task.done():
                state.agent_session.cancel_pending_tools()
                state.agent_task.cancel()
                state.turn_status = "failed"
            if state.realtime_stream_connected:
                try:
                    await _realtime_send(state, {"type": "response.cancel"})
                    await _realtime_send(state, {"type": "input_audio_buffer.clear"})
                except Exception:
                    pass
                if state.realtime_stream_turn and state.realtime_stream_turn.tts_playing_sent:
                    await websocket.send_json({"event": "tts_playing", "playing": False})
                state.realtime_stream_turn = None

        elif event == "start_lesson":
            # Client sends this after dispatching initial config (set_instructions etc.)
            if not (state.agent_task and not state.agent_task.done()):
                if state.phase == "intro" and not state.messages and auto_start:
                    # Fresh lesson: run intro.
                    state.agent_task = asyncio.create_task(auto_start())
                elif state.phase == "teaching" and not state.messages and state.first_teaching_task_fn:
                    # Sections exist but no messages (reconnect after intro but before first
                    # teaching turn was saved) — skip intro, start teaching directly.
                    state.agent_task = state.first_teaching_task_fn()

        elif event == "text_message":
            await _handle_text_message(websocket, msg, state, conn)

        elif event == "transcribe_only":
            await _handle_transcribe_only(websocket, msg, state)

        elif event == "voice_message":
            await _handle_voice_message(websocket, msg, state, conn)

        elif event == "run_code":
            asyncio.create_task(_handle_run_code(websocket, msg, state))

        elif event == "image_input":
            await _handle_image_input(websocket, msg, state, conn)

        elif event == "ping":
            await websocket.send_json({"event": "pong"})


# ── code execution handler ─────────────────────────────────────────────────────

_CODE_MAX_BYTES = 100_000       # 100 KB input limit
_OUTPUT_MAX_BYTES = 1_024_000   # 1 MB output limit


async def _handle_run_code(websocket: WebSocket, msg: dict, state: SessionState) -> None:
    """Stream sandboxed code execution output back to the client."""
    from ..services.agents.code_runner import stream_execution

    inv_id = msg.get("invocation_id", "")
    code = msg.get("code", "")
    runtime = msg.get("runtime", "python")

    if len(code.encode()) > _CODE_MAX_BYTES:
        await websocket.send_json(
            {"event": "code_error", "invocation_id": inv_id, "message": "Code exceeds 100 KB limit"}
        )
        return

    if not await _try_acquire(state.code_run_semaphore):
        await websocket.send_json(
            {"event": "code_error", "invocation_id": inv_id, "message": "Too many concurrent code executions — please wait"}
        )
        return

    output_bytes = 0
    try:
        async for ev in stream_execution(code, runtime):
            t = ev["type"]
            if t == "stdout":
                data = ev["data"]
                output_bytes += len(data.encode())
                if output_bytes > _OUTPUT_MAX_BYTES:
                    await websocket.send_json(
                        {"event": "code_stderr", "invocation_id": inv_id, "data": "\n[output truncated — 1 MB limit reached]"}
                    )
                    break
                await websocket.send_json(
                    {"event": "code_stdout", "invocation_id": inv_id, "data": data}
                )
            elif t == "stderr":
                data = ev["data"]
                output_bytes += len(data.encode())
                if output_bytes > _OUTPUT_MAX_BYTES:
                    await websocket.send_json(
                        {"event": "code_stderr", "invocation_id": inv_id, "data": "\n[output truncated — 1 MB limit reached]"}
                    )
                    break
                await websocket.send_json(
                    {"event": "code_stderr", "invocation_id": inv_id, "data": data}
                )
            elif t == "done":
                await websocket.send_json({
                    "event": "code_done",
                    "invocation_id": inv_id,
                    "exit_code": ev["exit_code"],
                    "elapsed_ms": ev["elapsed_ms"],
                })
            elif t == "error":
                await websocket.send_json(
                    {"event": "code_error", "invocation_id": inv_id, "message": ev["message"]}
                )
    except Exception as exc:
        log.exception("_handle_run_code raised: %s", exc)
        try:
            await websocket.send_json(
                {"event": "code_error", "invocation_id": inv_id, "message": str(exc)}
            )
        except Exception:
            pass
    finally:
        state.code_run_semaphore.release()


async def _try_acquire(sem: asyncio.Semaphore) -> bool:
    """Non-blocking semaphore acquire. Returns False immediately if all slots are taken."""
    try:
        await asyncio.wait_for(sem.acquire(), timeout=0)
        return True
    except (asyncio.TimeoutError, TimeoutError):
        return False


# ── STT model loader ───────────────────────────────────────────────────────────

_stt_load_lock: asyncio.Lock | None = None


async def _get_stt_model(state: SessionState) -> object:
    """Return the appropriate STT model, lazy-loading the default if not yet ready."""
    if state.stt_provider == "openai":
        # OpenAI STT is API-based and does not require a local model object.
        return object()
    global _stt_load_lock
    if state.stt_model_size and state.stt_model_size in app_state.stt_models:
        return app_state.stt_models[state.stt_model_size]
    if app_state.stt_model is not None:
        return app_state.stt_model
    # Lazy-load the default model on first use.
    if _stt_load_lock is None:
        _stt_load_lock = asyncio.Lock()
    async with _stt_load_lock:
        if app_state.stt_model is None:
            log.info("Lazy-loading STT model (%s)…", settings.STT_MODEL_SIZE)
            model = await asyncio.to_thread(load_stt_model, settings.STT_MODEL_SIZE)
            app_state.stt_model = model
            app_state.stt_models[settings.STT_MODEL_SIZE] = model
    return app_state.stt_model


def _evict_and_load_stt_model(model_size: str) -> object:
    """
    Evict all cached STT models, free CUDA memory, then load the requested model.
    Runs inside asyncio.to_thread() — blocking is fine here.
    """
    app_state.stt_models.clear()
    app_state.stt_model = None
    try:
        import gc
        import torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass

    return load_stt_model(model_size)


async def _load_stt_model_bg(websocket: WebSocket, model_size: str) -> None:
    """Load an STT model in a thread pool and cache it on app_state."""
    try:
        await websocket.send_json({"event": "status", "message": f"Loading STT model ({model_size})…"})
        model = await asyncio.to_thread(_evict_and_load_stt_model, model_size)
        app_state.stt_models[model_size] = model
        app_state.stt_model = model
        await websocket.send_json({"event": "status", "message": f"STT model ready ({model_size})"})
    except Exception as exc:
        log.error("Failed to load STT model %s: %s", model_size, exc)
        try:
            await websocket.send_json({"event": "error", "message": f"Failed to load STT model: {exc}"})
        except Exception:
            pass


# ── send loop ──────────────────────────────────────────────────────────────────

async def _send_loop(
    websocket: WebSocket,
    queue: asyncio.Queue,
    state: SessionState,
    auto_start,
) -> None:
    """Forward background-task events (e.g. decompose_complete) to the client."""
    while True:
        event = await queue.get()
        if event is None:
            break
        try:
            await websocket.send_json(event)
        except Exception:
            break

        # When decomposition finishes, refresh the in-memory curriculum and
        # kick off the appropriate next step.
        if event.get("event") == "decompose_complete":
            raw = event.get("curriculum", {})
            state.curriculum = Curriculum(
                title=raw.get("title", ""),
                sections=raw.get("sections", []),
                idx=0,
            )
            log.info(
                "[send_loop] decompose_complete: curriculum updated (%d sections)",
                len(state.curriculum.sections),
            )
            if not state.curriculum.sections:
                try:
                    await websocket.send_json({
                        "event": "error",
                        "message": "Decomposition finished without sections. Adjust the objective prompt and retry decomposition.",
                    })
                except Exception:
                    pass
                continue
            if not (state.agent_task and not state.agent_task.done()):
                if state.phase == "teaching" and state.first_teaching_task_fn:
                    # Goal already captured via intro — start first teaching turn.
                    state.agent_task = state.first_teaching_task_fn()
                elif not state.messages:
                    # Legacy / edge case: no intro yet, run it now.
                    state.agent_task = asyncio.create_task(auto_start())


# ── event handlers ─────────────────────────────────────────────────────────────

def _make_realtime_instructions(state: SessionState) -> str:
    """Lightweight instructions for the realtime session.
    The session is used as a VAD+STT relay — the chained teacher handles all
    reasoning — but keeping instructions ensures correct behaviour if the model
    ever speaks (e.g. a future fallback path) and keeps the persona consistent.
    """
    base = (
        "You are a helpful voice assistant relaying a teaching conversation. "
        "Keep any responses very short (one sentence). "
        "Plain prose only — no markdown or lists."
    )
    if state.agent_instructions:
        base += f"\n\n{state.agent_instructions}"
    return base


def _realtime_session_update_payload(state: SessionState) -> dict:
    """
    Configure the realtime session as a VAD + STT relay.
    create_response=False means the realtime model never generates a reply —
    transcripts are handed off to the chained teacher (Anthropic/Sonnet).
    voice and instructions are kept so the session is correctly configured
    if responses are ever re-enabled (e.g. fallback path).
    """
    return {
        "type": "session.update",
        "session": {
            "instructions": _make_realtime_instructions(state),
            "voice": settings.OPENAI_REALTIME_VOICE,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "modalities": ["text", "audio"],
            "turn_detection": {
                "type": "server_vad",
                "create_response": False,
                "threshold": settings.OPENAI_REALTIME_VAD_THRESHOLD,
                "prefix_padding_ms": settings.OPENAI_REALTIME_VAD_PREFIX_MS,
                "silence_duration_ms": settings.OPENAI_REALTIME_VAD_SILENCE_MS,
            },
            "input_audio_transcription": {"model": "gpt-4o-mini-transcribe"},
        },
    }


async def _realtime_send(state: SessionState, payload: dict) -> None:
    if not state.realtime_stream_connected or state.realtime_ws is None:
        raise RuntimeError("Realtime session is not connected.")
    async with state.realtime_send_lock:
        await state.realtime_ws.send(json.dumps(payload))


async def _send_realtime_session_update(state: SessionState) -> None:
    await _realtime_send(state, _realtime_session_update_payload(state))


def _next_realtime_turn(state: SessionState) -> RealtimeStreamTurn:
    turn = RealtimeStreamTurn(
        turn_id=str(uuid.uuid4()),
        turn_idx=state.realtime_turn_idx,
    )
    state.realtime_turn_idx += 1
    state.realtime_stream_turn = turn
    state.last_turn_id = turn.turn_id
    state.turn_status = "running"
    return turn


def _ensure_realtime_turn(state: SessionState) -> RealtimeStreamTurn:
    return state.realtime_stream_turn or _next_realtime_turn(state)


def _merge_realtime_text(existing: str, incoming: str) -> str:
    existing = (existing or "").strip()
    incoming = (incoming or "").strip()
    if not incoming:
        return existing
    if not existing:
        return incoming
    if incoming.startswith(existing):
        return incoming
    if existing.startswith(incoming):
        return existing
    if existing.endswith(incoming):
        return existing
    return f"{existing} {incoming}".strip()


async def _close_realtime_stream(state: SessionState, *, cancel_reader: bool = True) -> None:
    reader = state.realtime_reader_task
    ws = state.realtime_ws

    state.realtime_stream_connected = False
    state.realtime_streaming = False
    state.realtime_stream_turn = None
    state.realtime_ws = None

    if cancel_reader and reader and not reader.done():
        reader.cancel()
        try:
            await reader
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    state.realtime_reader_task = None

    if ws is not None:
        try:
            await ws.close()
        except Exception:
            pass


async def _ensure_realtime_stream_connected(
    websocket: WebSocket,
    state: SessionState,
    conn: aiosqlite.Connection,
) -> None:
    if state.realtime_stream_connected and state.realtime_ws is not None:
        return
    if state.phase != "teaching":
        raise RuntimeError("Realtime conversation is only available during teaching mode.")
    api_key = (settings.OPENAI_API_KEY or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured for realtime conversation.")

    ws_url = f"wss://api.openai.com/v1/realtime?model={quote(settings.OPENAI_REALTIME_MODEL)}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "OpenAI-Beta": "realtime=v1",
    }
    ws = await ws_connect(
        ws_url,
        additional_headers=headers,
        open_timeout=settings.OPENAI_REALTIME_TIMEOUT_S,
        close_timeout=settings.OPENAI_REALTIME_TIMEOUT_S,
        max_size=None,
    )
    state.realtime_ws = ws
    state.realtime_stream_connected = True
    await _send_realtime_session_update(state)
    state.realtime_reader_task = asyncio.create_task(_realtime_stream_reader(websocket, state, conn))


async def _emit_realtime_turn_start_if_needed(websocket: WebSocket, turn: RealtimeStreamTurn) -> None:
    if turn.turn_start_sent:
        return
    turn.turn_start_sent = True
    await websocket.send_json({"event": "turn_start"})


async def _finalize_realtime_stream_turn(
    websocket: WebSocket,
    state: SessionState,
    conn: aiosqlite.Connection,
    *,
    usage: dict,
) -> None:
    turn = state.realtime_stream_turn
    if turn is None:
        return

    user_text = turn.user_text.strip()
    assistant_text = turn.assistant_text.strip()

    if user_text and not turn.transcript_sent:
        turn.transcript_sent = True
        await websocket.send_json({"event": "transcription", "text": user_text, "turn_id": turn.turn_id})
    if not turn.turn_start_sent and (assistant_text or turn.chunk_idx > 0):
        await _emit_realtime_turn_start_if_needed(websocket, turn)

    if user_text:
        state.messages.append({"role": "user", "content": user_text})
    if assistant_text:
        state.messages.append({"role": "assistant", "content": assistant_text})

    if usage:
        try:
            usage_obj = usage_from_realtime(usage)
            app_state.token_tracker.record_api(
                call_type="realtime_turn",
                model=settings.OPENAI_REALTIME_MODEL,
                usage=usage_obj,
                user_id=state.agent_session._user_id,
                session_id=state.session_id,
            )
        except Exception:
            pass

    state.turn_status = "complete"
    await _save_state(conn, state)
    await websocket.send_json({
        "event": "chunk_complete",
        "turn_idx": turn.turn_idx,
        "chunk_idx": max(turn.chunk_idx - 1, 0),
    })
    if turn.tts_playing_sent:
        await websocket.send_json({"event": "tts_playing", "playing": False})
    await websocket.send_json({"event": "response_end"})
    await websocket.send_json({"event": "turn_complete", "turn_id": turn.turn_id})
    state.realtime_stream_turn = None


async def _dispatch_realtime_transcript_to_teacher(
    websocket: WebSocket,
    state: SessionState,
    conn: aiosqlite.Connection,
    text: str,
    turn_id: str,
) -> None:
    """
    Route a VAD-transcribed utterance through the chained teaching pipeline.

    The realtime session is used only for audio I/O (VAD + STT). All curriculum
    reasoning, tool calls, and TTS generation run through the existing chained
    teacher — so section advancement, student profiling, and slides all work.
    """
    if state.phase == "intro":
        if state.handle_intro_turn_fn:
            state.agent_task = asyncio.create_task(
                state.handle_intro_turn_fn(text, turn_id)
            )
        return

    # Drop stale trailing user messages from any previously failed turn.
    while state.messages and state.messages[-1].get("role") == "user":
        state.messages.pop()
    state.messages.append({"role": "user", "content": text})
    await websocket.send_json({"event": "status", "message": "Thinking..."})

    async def _run_turn() -> None:
        try:
            await state.agent_session.run_turn(
                state.curriculum, state.agent_instructions,
                lesson_goal=state.lesson_goal,
            )
            state.turn_status = "complete"
            await _save_state(conn, state)
            await websocket.send_json({"event": "turn_complete", "turn_id": turn_id})
            # Inject teacher reply into realtime session so it has conversation context.
            if state.realtime_stream_connected and state.realtime_ws:
                teacher_text = next(
                    (m.get("content", "") for m in reversed(state.messages)
                     if m.get("role") == "assistant" and isinstance(m.get("content"), str)),
                    "",
                )
                if teacher_text:
                    try:
                        await _realtime_send(state, {
                            "type": "conversation.item.create",
                            "item": {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "text", "text": teacher_text}],
                            },
                        })
                    except Exception:
                        pass  # Non-critical — context injection only
        except asyncio.CancelledError:
            state.turn_status = "failed"
        except Exception as exc:
            log.exception("[realtime-teacher] turn raised")
            state.turn_status = "failed"
            await websocket.send_json({"event": "error", "message": str(exc) or type(exc).__name__})

    state.agent_task = asyncio.create_task(_run_turn())


async def _realtime_stream_reader(
    websocket: WebSocket,
    state: SessionState,
    conn: aiosqlite.Connection,
) -> None:
    try:
        while state.realtime_stream_connected and state.realtime_ws is not None:
            raw = await state.realtime_ws.recv()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="ignore")
            event = json.loads(raw)
            etype = event.get("type", "")

            if etype == "conversation.item.input_audio_transcription.completed":
                text = (event.get("transcript") or "").strip()
                if text:
                    turn = _ensure_realtime_turn(state)
                    turn.user_text = _merge_realtime_text(turn.user_text, text)
                    if not turn.transcript_sent:
                        turn.transcript_sent = True
                        await websocket.send_json({"event": "transcription", "text": turn.user_text, "turn_id": turn.turn_id})
                    state.realtime_stream_turn = None  # reset for next utterance
                    # Hand off to chained teacher if not already running a turn.
                    if not (state.agent_task and not state.agent_task.done()):
                        state.last_turn_id = turn.turn_id
                        state.turn_status = "running"
                        await _dispatch_realtime_transcript_to_teacher(
                            websocket, state, conn, turn.user_text, turn.turn_id
                        )

            elif etype == "error":
                err = event.get("error") or {}
                msg = err.get("message") or str(err) or "Unknown realtime error"
                await websocket.send_json({"event": "error", "message": f"Realtime stream error: {msg}"})
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"event": "error", "message": f"Realtime stream disconnected: {exc}"})
        except Exception:
            pass
    finally:
        await _close_realtime_stream(state, cancel_reader=False)


async def _handle_realtime_stream_start(
    websocket: WebSocket,
    state: SessionState,
    conn: aiosqlite.Connection,
) -> None:
    if state.voice_arch != "realtime":
        await websocket.send_json({
            "event": "status",
            "message": "Conversation mode is chained. Switch to realtime to stream audio.",
        })
        return
    try:
        await _ensure_realtime_stream_connected(websocket, state, conn)
        state.realtime_streaming = True
        await websocket.send_json({"event": "status", "message": "Realtime stream connected."})
    except Exception as exc:
        await websocket.send_json({"event": "error", "message": f"Could not start realtime stream: {exc}"})


async def _handle_realtime_stream_chunk(
    websocket: WebSocket,
    msg: dict,
    state: SessionState,
    conn: aiosqlite.Connection,
) -> None:
    if state.voice_arch != "realtime":
        return
    audio_b64: str = msg.get("data", "")
    sample_rate: int = int(msg.get("sample_rate", 16000) or 16000)
    if not audio_b64:
        return
    try:
        await _ensure_realtime_stream_connected(websocket, state, conn)
        state.realtime_streaming = True
        pcm16_b64 = float32_b64_to_pcm16_b64(
            audio_b64,
            sample_rate=sample_rate,
            target_sample_rate=settings.OPENAI_REALTIME_SAMPLE_RATE,
        )
        await _realtime_send(state, {"type": "input_audio_buffer.append", "audio": pcm16_b64})
    except Exception as exc:
        await websocket.send_json({"event": "error", "message": f"Realtime stream chunk failed: {exc}"})


async def _handle_realtime_stream_stop(
    websocket: WebSocket,
    state: SessionState,
) -> None:
    state.realtime_streaming = False
    if not state.realtime_stream_connected:
        return
    try:
        await _realtime_send(state, {"type": "input_audio_buffer.commit"})
    except Exception as exc:
        await websocket.send_json({"event": "error", "message": f"Realtime stream stop failed: {exc}"})


async def _handle_audio_input(
    websocket: WebSocket,
    msg: dict,
    state: SessionState,
    conn: aiosqlite.Connection,
) -> None:
    """
    Entry point for mic utterances.

    - `VOICE_ARCH=chained`  -> existing STT -> agent -> TTS pipeline.
    - `VOICE_ARCH=realtime` -> OpenAI Realtime audio in/out (teaching phase),
      then automatic fallback to chained on any realtime failure.
    """
    if (
        state.voice_arch == "realtime"
        and state.phase == "teaching"
        and not (state.agent_task and not state.agent_task.done())
    ):
        audio_b64: str = msg.get("data", "")
        sample_rate: int = msg.get("sample_rate", 16000)
        turn_id = str(uuid.uuid4())
        state.last_turn_id = turn_id
        state.turn_status = "running"

        async def _run_realtime() -> None:
            try:
                await _handle_audio_input_realtime_turn(
                    websocket=websocket,
                    state=state,
                    conn=conn,
                    audio_b64=audio_b64,
                    sample_rate=sample_rate,
                    turn_id=turn_id,
                )
            except asyncio.CancelledError:
                state.turn_status = "failed"
                try:
                    await websocket.send_json({"event": "tts_playing", "playing": False})
                    await websocket.send_json({"event": "turn_interrupted"})
                except Exception:
                    pass
            except Exception as exc:
                log.warning("Realtime turn failed; falling back to chained path: %s", exc)
                try:
                    await websocket.send_json({
                        "event": "status",
                        "message": "Realtime unavailable. Falling back to chained speech path…",
                    })
                    # Preserve a stable turn id for UI continuity.
                    state.last_turn_id = turn_id
                    # Clear the in-flight realtime marker so chained fallback
                    # can pass its "turn already in progress" guard.
                    state.agent_task = None
                    await _handle_audio_input_chained(websocket, msg, state, conn)
                except Exception as chained_exc:
                    state.turn_status = "failed"
                    await websocket.send_json({"event": "error", "message": f"Realtime+fallback failed: {chained_exc}"})

        state.agent_task = asyncio.create_task(_run_realtime())
        return

    await _handle_audio_input_chained(websocket, msg, state, conn)


async def _handle_audio_input_realtime_turn(
    *,
    websocket: WebSocket,
    state: SessionState,
    conn: aiosqlite.Connection,
    audio_b64: str,
    sample_rate: int,
    turn_id: str,
) -> None:
    """Process one audio utterance via OpenAI Realtime and stream it to client."""
    turn_idx = state.realtime_turn_idx
    state.realtime_turn_idx += 1
    chunk_idx = 0
    transcript_sent = False
    tts_playing_sent = False

    # Reuse the section-aware teaching system prompt to keep behavior aligned.
    from ..services.agents.prompts.teaching import make_teaching_prompt

    system = make_teaching_prompt(
        state.curriculum.title,
        state.curriculum.sections,
        state.curriculum.idx,
        state.lesson_goal,
    )
    if state.agent_instructions:
        system += f"\n\nADDITIONAL STYLE INSTRUCTIONS:\n{state.agent_instructions}"

    await websocket.send_json({"event": "status", "message": "Realtime listening…"})
    await websocket.send_json({"event": "turn_start"})

    async def _emit_user_transcript(text: str) -> None:
        nonlocal transcript_sent
        if text and not transcript_sent:
            transcript_sent = True
            await websocket.send_json({"event": "transcription", "text": text, "turn_id": turn_id})

    async def _emit_text_delta(text: str) -> None:
        if text:
            await websocket.send_json({"event": "text_chunk", "text": text, "turn_idx": turn_idx})

    async def _emit_audio_chunk(audio: object) -> None:
        nonlocal chunk_idx, tts_playing_sent
        try:
            import numpy as _np
            chunk = _np.asarray(audio, dtype=_np.float32)
        except Exception:
            return
        if chunk.size == 0:
            return
        if not tts_playing_sent:
            tts_playing_sent = True
            await websocket.send_json({"event": "tts_playing", "playing": True})
        data = base64.b64encode(chunk.tobytes()).decode()
        await websocket.send_json({
            "event": "audio_chunk",
            "data": data,
            "sample_rate": settings.OPENAI_REALTIME_SAMPLE_RATE,
            "turn_idx": turn_idx,
            "chunk_idx": chunk_idx,
        })
        chunk_idx += 1

    summary = await run_realtime_voice_turn(
        audio_b64=audio_b64,
        sample_rate=sample_rate,
        api_key=settings.OPENAI_API_KEY,
        model=settings.OPENAI_REALTIME_MODEL,
        voice=settings.OPENAI_REALTIME_VOICE,
        instructions=system,
        target_sample_rate=settings.OPENAI_REALTIME_SAMPLE_RATE,
        timeout_seconds=settings.OPENAI_REALTIME_TIMEOUT_S,
        max_retries=settings.OPENAI_REALTIME_MAX_RETRIES,
        on_user_transcript=_emit_user_transcript,
        on_text_delta=_emit_text_delta,
        on_audio_chunk=_emit_audio_chunk,
    )

    user_text = (summary.user_transcript or "").strip()
    assistant_text = (summary.assistant_text or "").strip()

    if not transcript_sent and user_text:
        await websocket.send_json({"event": "transcription", "text": user_text, "turn_id": turn_id})
        transcript_sent = True

    if user_text:
        state.messages.append({"role": "user", "content": user_text})
    if assistant_text:
        state.messages.append({"role": "assistant", "content": assistant_text})

    # Track OpenAI realtime token usage in the existing usage pipeline.
    try:
        usage_obj = usage_from_realtime(summary.usage)
        app_state.token_tracker.record_api(
            call_type="realtime_turn",
            model=settings.OPENAI_REALTIME_MODEL,
            usage=usage_obj,
            user_id=state.agent_session._user_id,
            session_id=state.session_id,
        )
    except Exception:
        pass

    state.turn_status = "complete"
    await _save_state(conn, state)
    await websocket.send_json({"event": "chunk_complete", "turn_idx": turn_idx, "chunk_idx": max(chunk_idx - 1, 0)})
    if tts_playing_sent:
        await websocket.send_json({"event": "tts_playing", "playing": False})
    await websocket.send_json({"event": "response_end"})
    await websocket.send_json({"event": "turn_complete", "turn_id": turn_id})


async def _handle_audio_input_chained(
    websocket: WebSocket,
    msg: dict,
    state: SessionState,
    conn: aiosqlite.Connection,
) -> None:
    """Transcribe audio, append to messages, launch agent turn."""
    if state.agent_task and not state.agent_task.done():
        await websocket.send_json({
            "event": "error",
            "message": "Agent turn already in progress",
        })
        return

    audio_b64: str = msg.get("data", "")
    sample_rate: int = msg.get("sample_rate", 16000)

    await websocket.send_json({"event": "status", "message": "Transcribing..."})

    stt_provider = state.stt_provider
    _stt_t0 = time.monotonic()
    try:
        if stt_provider == "openai":
            model_id = state.stt_model_size or settings.OPENAI_STT_MODEL
            text = await transcribe_openai(
                audio_b64,
                sample_rate,
                api_key=settings.OPENAI_API_KEY,
                model=model_id,
                language=state.stt_language,
                timeout_seconds=settings.OPENAI_STT_TIMEOUT_S,
                max_retries=settings.OPENAI_STT_MAX_RETRIES,
                cost_per_minute_usd=settings.OPENAI_STT_COST_PER_MINUTE_USD,
                user_id=state.agent_session._user_id,
            )
        else:
            stt_model = await _get_stt_model(state)
            text = await transcribe(
                audio_b64, sample_rate, stt_model, state.stt_language, user_id=state.agent_session._user_id
            )
    except Exception as exc:
        await websocket.send_json({"event": "error", "message": f"STT error: {exc}"})
        return
    log.info("[stt] provider=%s elapsed=%.2fs chars=%d", stt_provider, time.monotonic() - _stt_t0, len(text))

    # Re-check: another handler may have started a turn while we were transcribing.
    if state.agent_task and not state.agent_task.done():
        await websocket.send_json({"event": "error", "message": "Agent turn already in progress"})
        return

    if not text.strip():
        await websocket.send_json({"event": "status", "message": "Ready"})
        return

    import uuid as _uuid
    turn_id = str(_uuid.uuid4())
    state.last_turn_id = turn_id
    state.turn_status = "running"

    await websocket.send_json({
        "event": "transcription",
        "text": text,
        "turn_id": turn_id,
    })

    # ── Intro phase: continue the goal-gathering loop ────────────────────────
    if state.phase == "intro":
        if state.handle_intro_turn_fn:
            state.agent_task = asyncio.create_task(
                state.handle_intro_turn_fn(text, turn_id)
            )
        return

    # ── Teaching phase: normal conversation turn ────────────────────────────
    while state.messages and state.messages[-1].get("role") == "user":
        state.messages.pop()
    state.messages.append({"role": "user", "content": text})
    await websocket.send_json({"event": "status", "message": "Thinking..."})

    async def _run_turn() -> None:
        _turn_t0 = time.monotonic()
        log.info("[turn %s] starting agent turn (messages=%d)", turn_id, len(state.messages))
        try:
            await state.agent_session.run_turn(
                state.curriculum,
                state.agent_instructions,
                lesson_goal=state.lesson_goal,
            )
            log.info("[turn %s] complete elapsed=%.2fs", turn_id, time.monotonic() - _turn_t0)
            state.turn_status = "complete"
            await _save_state(conn, state)
            await websocket.send_json({"event": "turn_complete", "turn_id": turn_id})
        except asyncio.CancelledError:
            log.info("[turn %s] cancelled elapsed=%.2fs", turn_id, time.monotonic() - _turn_t0)
            state.turn_status = "failed"
        except Exception as exc:
            log.exception("[turn %s] raised after %.2fs", turn_id, time.monotonic() - _turn_t0)
            state.turn_status = "failed"
            await websocket.send_json({"event": "error", "message": str(exc) or type(exc).__name__})

    state.agent_task = asyncio.create_task(_run_turn())


async def _handle_text_message(
    websocket: WebSocket,
    msg: dict,
    state: SessionState,
    conn: aiosqlite.Connection,
) -> None:
    """Handle a typed text message from the client — runs an agent turn directly."""
    if state.agent_task and not state.agent_task.done():
        await websocket.send_json({"event": "error", "message": "Agent turn already in progress"})
        return

    text: str = msg.get("text", "").strip()
    if not text:
        return

    import uuid as _uuid
    turn_id = str(_uuid.uuid4())
    state.last_turn_id = turn_id
    state.turn_status = "running"

    await websocket.send_json({"event": "transcription", "text": text, "turn_id": turn_id})

    if state.phase == "intro":
        if state.handle_intro_turn_fn:
            state.agent_task = asyncio.create_task(
                state.handle_intro_turn_fn(text, turn_id)
            )
        return

    # Strip any orphaned trailing user messages left by a previously failed turn.
    while state.messages and state.messages[-1].get("role") == "user":
        state.messages.pop()
    state.messages.append({"role": "user", "content": text})
    await websocket.send_json({"event": "status", "message": "Thinking..."})

    async def _run_turn() -> None:
        try:
            await state.agent_session.run_turn(
                state.curriculum, state.agent_instructions,
                lesson_goal=state.lesson_goal,
            )
            state.turn_status = "complete"
            await _save_state(conn, state)
            await websocket.send_json({"event": "turn_complete", "turn_id": turn_id})
        except asyncio.CancelledError:
            state.turn_status = "failed"
        except Exception as exc:
            log.exception("[text_message] agent turn raised")
            state.turn_status = "failed"
            await websocket.send_json({"event": "error", "message": str(exc) or type(exc).__name__})

    state.agent_task = asyncio.create_task(_run_turn())


async def _handle_transcribe_only(
    websocket: WebSocket,
    msg: dict,
    state: SessionState,
) -> None:
    """Run STT and return transcription to the client without starting an agent turn."""
    audio_b64: str = msg.get("data", "")
    sample_rate: int = msg.get("sample_rate", 16000)
    try:
        if state.stt_provider == "openai":
            model_id = state.stt_model_size or settings.OPENAI_STT_MODEL
            text = await transcribe_openai(
                audio_b64,
                sample_rate,
                api_key=settings.OPENAI_API_KEY,
                model=model_id,
                language=state.stt_language,
                timeout_seconds=settings.OPENAI_STT_TIMEOUT_S,
                max_retries=settings.OPENAI_STT_MAX_RETRIES,
                cost_per_minute_usd=settings.OPENAI_STT_COST_PER_MINUTE_USD,
                user_id=state.agent_session._user_id,
            )
        else:
            stt_model = await _get_stt_model(state)
            text = await transcribe(
                audio_b64, sample_rate, stt_model, state.stt_language, user_id=state.agent_session._user_id
            )
        await websocket.send_json({"event": "transcription_only", "text": text})
    except Exception as exc:
        await websocket.send_json({"event": "error", "message": f"STT error: {exc}"})


async def _handle_voice_message(
    websocket: WebSocket,
    msg: dict,
    state: SessionState,
    conn: aiosqlite.Connection,
) -> None:
    """
    Transcribe a compressed audio file (webm/opus etc.) and run an agent turn.

    The Claude API does not yet accept raw audio content blocks, so we run the
    recording through Whisper first, then proceed exactly like a typed message.
    """
    if state.agent_task and not state.agent_task.done():
        await websocket.send_json({"event": "error", "message": "Agent turn already in progress"})
        return

    audio_b64: str = msg.get("data", "")
    mime_type: str = msg.get("mime_type", "audio/webm")

    stt_provider = state.stt_provider
    try:
        if stt_provider == "openai":
            model_id = state.stt_model_size or settings.OPENAI_STT_MODEL
            text = await transcribe_file_openai(
                audio_b64,
                mime_type,
                api_key=settings.OPENAI_API_KEY,
                model=model_id,
                language=state.stt_language,
                timeout_seconds=settings.OPENAI_STT_TIMEOUT_S,
                max_retries=settings.OPENAI_STT_MAX_RETRIES,
                cost_per_minute_usd=settings.OPENAI_STT_COST_PER_MINUTE_USD,
                user_id=state.agent_session._user_id,
            )
        else:
            stt_model = await _get_stt_model(state)
            text = await transcribe_file(
                audio_b64, mime_type, stt_model, state.stt_language, user_id=state.agent_session._user_id
            )
    except Exception as exc:
        await websocket.send_json({"event": "error", "message": f"STT error: {exc}"})
        return

    # Re-check: another handler may have started a turn while we were transcribing.
    if state.agent_task and not state.agent_task.done():
        await websocket.send_json({"event": "error", "message": "Agent turn already in progress"})
        return

    if not text.strip():
        await websocket.send_json({"event": "status", "message": "Ready"})
        return

    import uuid as _uuid
    turn_id = str(_uuid.uuid4())
    state.last_turn_id = turn_id
    state.turn_status = "running"

    await websocket.send_json({"event": "transcription", "text": text, "turn_id": turn_id})

    if state.phase == "intro":
        if state.handle_intro_turn_fn:
            state.agent_task = asyncio.create_task(
                state.handle_intro_turn_fn(text, turn_id)
            )
        return

    while state.messages and state.messages[-1].get("role") == "user":
        state.messages.pop()
    state.messages.append({"role": "user", "content": text})
    await websocket.send_json({"event": "status", "message": "Thinking..."})

    async def _run_turn() -> None:
        try:
            await state.agent_session.run_turn(
                state.curriculum, state.agent_instructions,
                lesson_goal=state.lesson_goal,
            )
            state.turn_status = "complete"
            await _save_state(conn, state)
            await websocket.send_json({"event": "turn_complete", "turn_id": turn_id})
        except asyncio.CancelledError:
            state.turn_status = "failed"
        except Exception as exc:
            log.exception("[voice_message] agent turn raised")
            state.turn_status = "failed"
            await websocket.send_json({"event": "error", "message": str(exc) or type(exc).__name__})

    state.agent_task = asyncio.create_task(_run_turn())


async def _handle_image_input(
    websocket: WebSocket,
    msg: dict,
    state: SessionState,
    conn: aiosqlite.Connection,
) -> None:
    """Student submitted an annotation/photo from the slide viewer (not agent-requested)."""
    if state.agent_task and not state.agent_task.done():
        await websocket.send_json({"event": "error", "message": "Agent turn already in progress"})
        return

    data: str = msg.get("data", "")
    caption: str = msg.get("caption", "The student has annotated the slide.")
    if not data:
        return

    import uuid as _uuid
    turn_id = str(_uuid.uuid4())
    state.last_turn_id = turn_id
    state.turn_status = "running"

    state.messages.append({
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": data},
            },
            {"type": "text", "text": caption},
        ],
    })

    async def _run_annotation_turn() -> None:
        log.info("[turn %s] annotation turn (messages=%d)", turn_id, len(state.messages))
        try:
            await state.agent_session.run_turn(
                state.curriculum,
                state.agent_instructions,
                lesson_goal=state.lesson_goal,
            )
            state.turn_status = "complete"
            await _save_state(conn, state)
            await websocket.send_json({"event": "turn_complete", "turn_id": turn_id})
        except asyncio.CancelledError:
            state.turn_status = "failed"
        except Exception as exc:
            log.exception("[turn %s] annotation turn raised", turn_id)
            state.turn_status = "failed"
            await websocket.send_json({"event": "error", "message": str(exc) or type(exc).__name__})

    state.agent_task = asyncio.create_task(_run_annotation_turn())


async def _handle_reconnect(
    websocket: WebSocket,
    msg: dict,
    state: SessionState,
) -> None:
    """Inform the reconnecting client of the last known turn status."""
    client_turn_id = msg.get("last_turn_id")
    if client_turn_id == state.last_turn_id and state.turn_status == "running":
        # Auto-cancel the stuck turn so new messages can be sent immediately.
        state.agent_session.cancel_pending_tools()
        if state.agent_task and not state.agent_task.done():
            state.agent_task.cancel()
        state.turn_status = "failed"
        await websocket.send_json({"event": "turn_interrupted"})
    else:
        await websocket.send_json({
            "event": "reconnect_ack",
            "turn_status": state.turn_status,
            "curriculum": {
                "title": state.curriculum.title,
                "idx": state.curriculum.idx,
                "total": len(state.curriculum.sections),
            },
        })

    # If the agent is mid-turn waiting on an interactive tool, re-open that tool UI.
    pending = state.agent_session.pending_tool_event
    if pending is not None:
        await websocket.send_json(pending)


# ── persistence ────────────────────────────────────────────────────────────────

async def _save_state(
    conn: aiosqlite.Connection, state: SessionState
) -> None:
    await models.update_enrollment(
        conn,
        state.enrollment_id,
        current_section_idx=state.curriculum.idx,
        completed=int(state.curriculum.is_last and state.turn_status == "complete"),
    )
    await models.upsert_messages(conn, state.enrollment_id, state.messages)


# ── helpers ────────────────────────────────────────────────────────────────────

def _build_history_turns(
    messages: list[dict],
    enrollment_assets: list[dict] | None = None,
) -> list[dict]:
    """
    Convert raw LLM messages into display turns for the history event.

    Rules:
    - Assistant text blocks become the turn's text; tool_use show_slide blocks
      add a slide figure; sketchpad/take_photo tool_use blocks record the prompt.
    - generate_visual_aid tool_use blocks add an image figure (URL from enrollment_assets).
    - User tool_result blocks with a base64 image become drawing/photo figures.
    - User tool_result "OK" placeholders (no image) are dropped silently.
    - Plain user text messages (transcriptions) become user turns with text.
    - Turns with neither text nor figures are omitted.
    """
    turns: list[dict] = []
    # Map tool_use_id → prompt for sketchpad / take_photo tool calls.
    pending_prompt: dict[str, str] = {}
    # Map tool_use_id → asset row for generate_visual_aid.
    assets_by_tool_id: dict[str, dict] = {
        a["tool_use_id"]: a for a in (enrollment_assets or []) if a.get("tool_use_id")
    }

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")

        if role == "assistant":
            if isinstance(content, str):
                if content.strip():
                    turns.append({"role": "assistant", "text": content})
                continue
            if not isinstance(content, list):
                continue

            text_parts: list[str] = []
            figures: list[dict] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    t = block.get("text", "")
                    if t:
                        text_parts.append(t)
                elif btype == "tool_use":
                    name = block.get("name", "")
                    inp = block.get("input", {})
                    tool_id = block.get("id", "")
                    if name == "show_slide":
                        page_s = inp.get("page_start", inp.get("page_number", 1))
                        figures.append({
                            "type": "slide",
                            "page": page_s,
                            "caption": inp.get("caption", ""),
                        })
                    elif name in ("open_sketchpad", "take_photo"):
                        pending_prompt[tool_id] = inp.get("prompt", "")
                    elif name == "generate_visual_aid":
                        asset = assets_by_tool_id.get(tool_id)
                        if asset and asset.get("image_path"):
                            figures.append({
                                "type": "generated_image",
                                "image_url": f"/api/lessons/assets/{asset['image_path']}",
                                "caption": inp.get("caption", ""),
                                "prompt": inp.get("prompt", ""),
                            })

            text = " ".join(text_parts)
            if text or figures:
                turn: dict = {"role": "assistant", "text": text}
                if figures:
                    turn["figures"] = figures
                turns.append(turn)

        elif role == "user":
            if isinstance(content, str):
                if content.strip():
                    turns.append({"role": "user", "text": content})
                continue
            if not isinstance(content, list):
                continue

            text_parts = []
            figures = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    tool_use_id = block.get("tool_use_id", "")
                    prompt = pending_prompt.pop(tool_use_id, "")
                    inner = block.get("content", "")
                    if isinstance(inner, list):
                        for ib in inner:
                            if isinstance(ib, dict) and ib.get("type") == "image":
                                src = ib.get("source", {})
                                if src.get("type") == "base64":
                                    figures.append({
                                        "type": "drawing",
                                        "data": src.get("data", ""),
                                        "prompt": prompt,
                                    })

            text = " ".join(text_parts)
            if text or figures:
                turn = {"role": "user", "text": text}
                if figures:
                    turn["figures"] = figures
                turns.append(turn)

    return turns


def _section_to_dict(row: dict) -> dict:
    """Convert a DB section row back to the format TeachingAgent expects."""
    return {
        "title": row.get("title", ""),
        "content": row.get("content", ""),
        "key_concepts": row.get("key_concepts", []),
        "page_start": row.get("page_start"),
        "page_end": row.get("page_end"),
    }
