"""
WebSocket session handler for the backend.

One WebSocket connection per teaching session.  The handler runs two concurrent
loops:
  - receive_loop: processes inbound messages from the frontend server
  - send_loop: forwards events from the per-session asyncio.Queue to the client

The TeachingAgent runs inside asyncio.to_thread() so it never blocks the event
loop.  Agent callbacks schedule WS sends back onto the event loop via
asyncio.run_coroutine_threadsafe() (see BackendAgentSession in services/agent.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated

log = logging.getLogger(__name__)

import aiosqlite
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from ..app_state import app_state, registry
from ..config import settings
from ..db import connection as db, models
from ..services.agent import BackendAgentSession
from ..services.stt import transcribe, load_stt_model
from shared.lesson import Curriculum

router = APIRouter(tags=["ws"])


# ── dependency shorthand ───────────────────────────────────────────────────────

Conn = Annotated[aiosqlite.Connection, Depends(db.get)]


# ── per-session state ──────────────────────────────────────────────────────────

class SessionState:
    """Mutable state for one active teaching session."""

    def __init__(
        self,
        session_id: str,
        lesson_id: str,
        curriculum: Curriculum,
        messages: list[dict],
        agent_instructions: str | None,
        agent_session: BackendAgentSession,
        pdf_path: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.lesson_id = lesson_id
        self.curriculum = curriculum
        self.messages = messages
        self.agent_instructions = agent_instructions
        self.agent_session = agent_session
        self.pdf_path = pdf_path
        self.agent_task: asyncio.Task | None = None
        self.last_turn_id: str | None = None
        self.turn_status: str = "idle"  # 'idle' | 'running' | 'complete' | 'failed'
        self.stt_language: str | None = None  # None = auto-detect
        self.stt_model_size: str | None = None  # None = use app default
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

    # Load lesson
    lesson = await models.get_lesson(conn, lesson_id)
    if lesson is None:
        await websocket.send_json({"event": "error", "message": "Lesson not found"})
        await websocket.close(code=4004)
        return

    # Verify lesson belongs to the session's user
    if lesson["user_id"] != session["user_id"]:
        await websocket.send_json({"event": "error", "message": "Access denied"})
        await websocket.close(code=4003)
        return

    sections = await models.get_sections(conn, lesson_id)
    messages = await models.get_messages(conn, lesson_id)
    curriculum = Curriculum(
        title=lesson["title"],
        sections=[_section_to_dict(s) for s in sections],
        idx=lesson["current_section_idx"],
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
        history_turns = _build_history_turns(messages)
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
    agent_session = BackendAgentSession(
        send=_send,
        loop=loop,
        kokoro_pipeline=app_state.kokoro_pipeline,
        kokoro_voice=settings.DEFAULT_VOICE,
        llm_model=settings.LLM_MODEL,
        pdf_path=pdf_full_path,
        session_id=session_id,
        user_id=session.get("user_id", ""),
    )

    state = SessionState(
        session_id=session_id,
        lesson_id=lesson_id,
        curriculum=curriculum,
        messages=messages,
        agent_instructions=None,  # set by client via set_instructions event
        agent_session=agent_session,
        pdf_path=pdf_full_path,
    )
    # Restore persisted lesson_goal (captured during intro, saved before decomposition).
    state.lesson_goal = lesson.get("lesson_goal") or None
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
                await models.update_lesson(conn, lesson_id, lesson_goal=goal)
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
                await models.update_lesson(conn, lesson_id, lesson_goal=goal)
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
                state.messages,
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

        done, pending = await asyncio.wait(
            {receive_task, send_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()

    except WebSocketDisconnect:
        pass
    finally:
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

        elif event == "set_voice":
            voice = msg.get("voice")
            if voice:
                state.agent_session.agent.kokoro_voice = voice

        elif event == "set_stt_language":
            lang = msg.get("language")  # BCP-47 code or None / "" for auto
            state.stt_language = lang or None

        elif event == "set_stt_model":
            size = msg.get("model_size")
            if size:
                state.stt_model_size = size
                if size not in app_state.stt_models:
                    asyncio.create_task(_load_stt_model_bg(websocket, size))

        elif event == "reconnect":
            await _handle_reconnect(websocket, msg, state)

        elif event == "cancel_turn":
            if state.agent_task and not state.agent_task.done():
                state.agent_task.cancel()
                state.turn_status = "failed"

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
    from ..services.code_runner import stream_execution

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
            if not (state.agent_task and not state.agent_task.done()):
                if state.phase == "teaching" and state.first_teaching_task_fn:
                    # Goal already captured via intro — start first teaching turn.
                    state.agent_task = state.first_teaching_task_fn()
                elif not state.messages:
                    # Legacy / edge case: no intro yet, run it now.
                    state.agent_task = asyncio.create_task(auto_start())


# ── event handlers ─────────────────────────────────────────────────────────────

async def _handle_audio_input(
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

    try:
        stt_model = await _get_stt_model(state)
        text = await transcribe(audio_b64, sample_rate, stt_model, state.stt_language)
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
    state.messages.append({"role": "user", "content": text})
    await websocket.send_json({"event": "status", "message": "Thinking..."})

    async def _run_turn() -> None:
        log.info("[turn %s] starting agent turn (messages=%d)", turn_id, len(state.messages))
        try:
            await state.agent_session.run_turn(
                state.curriculum,
                state.messages,
                state.agent_instructions,
                lesson_goal=state.lesson_goal,
            )
            log.info("[turn %s] agent turn complete", turn_id)
            state.turn_status = "complete"
            await _save_state(conn, state)
            await websocket.send_json({"event": "turn_complete", "turn_id": turn_id})
        except asyncio.CancelledError:
            log.info("[turn %s] agent turn cancelled", turn_id)
            state.turn_status = "failed"
        except Exception as exc:
            log.exception("[turn %s] agent turn raised", turn_id)
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

    state.messages.append({"role": "user", "content": text})
    await websocket.send_json({"event": "status", "message": "Thinking..."})

    async def _run_turn() -> None:
        try:
            await state.agent_session.run_turn(
                state.curriculum, state.messages, state.agent_instructions,
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
    from ..services.stt import transcribe
    audio_b64: str = msg.get("data", "")
    sample_rate: int = msg.get("sample_rate", 16000)
    try:
        stt_model = await _get_stt_model(state)
        text = await transcribe(audio_b64, sample_rate, stt_model, state.stt_language)
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
    from ..services.stt import transcribe_file
    if state.agent_task and not state.agent_task.done():
        await websocket.send_json({"event": "error", "message": "Agent turn already in progress"})
        return

    audio_b64: str = msg.get("data", "")
    mime_type: str = msg.get("mime_type", "audio/webm")

    try:
        stt_model = await _get_stt_model(state)
        text = await transcribe_file(audio_b64, mime_type, stt_model, state.stt_language)
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

    state.messages.append({"role": "user", "content": text})
    await websocket.send_json({"event": "status", "message": "Thinking..."})

    async def _run_turn() -> None:
        try:
            await state.agent_session.run_turn(
                state.curriculum, state.messages, state.agent_instructions,
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
                state.messages,
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


# ── persistence ────────────────────────────────────────────────────────────────

async def _save_state(
    conn: aiosqlite.Connection, state: SessionState
) -> None:
    from shared.lesson import LessonStore
    await models.update_lesson(
        conn,
        state.lesson_id,
        current_section_idx=state.curriculum.idx,
        completed=int(state.curriculum.is_last and state.turn_status == "complete"),
    )
    await models.upsert_messages(conn, state.lesson_id, state.messages)


# ── helpers ────────────────────────────────────────────────────────────────────

def _build_history_turns(messages: list[dict]) -> list[dict]:
    """
    Convert raw LLM messages into display turns for the history event.

    Rules:
    - Assistant text blocks become the turn's text; tool_use show_slide blocks
      add a slide figure; sketchpad/take_photo tool_use blocks record the prompt.
    - User tool_result blocks with a base64 image become drawing/photo figures.
    - User tool_result "OK" placeholders (no image) are dropped silently.
    - Plain user text messages (transcriptions) become user turns with text.
    - Turns with neither text nor figures are omitted.
    """
    turns: list[dict] = []
    # Map tool_use_id → prompt for sketchpad / take_photo tool calls.
    pending_prompt: dict[str, str] = {}

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
                    if name == "show_slide":
                        page_s = inp.get("page_start", inp.get("page_number", 1))
                        figures.append({
                            "type": "slide",
                            "page": page_s,
                            "caption": inp.get("caption", ""),
                        })
                    elif name in ("open_sketchpad", "take_photo"):
                        pending_prompt[block.get("id", "")] = inp.get("prompt", "")

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
