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
from ..services.stt import transcribe
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
    ) -> None:
        self.session_id = session_id
        self.lesson_id = lesson_id
        self.curriculum = curriculum
        self.messages = messages
        self.agent_instructions = agent_instructions
        self.agent_session = agent_session
        self.agent_task: asyncio.Task | None = None
        self.last_turn_id: str | None = None
        self.turn_status: str = "idle"  # 'idle' | 'running' | 'complete' | 'failed'
        self.stt_language: str | None = None  # None = auto-detect
        self.phase: str = "intro"  # 'intro' | 'teaching'
        self.lesson_goal: str | None = None


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
    )

    state = SessionState(
        session_id=session_id,
        lesson_id=lesson_id,
        curriculum=curriculum,
        messages=messages,
        agent_instructions=None,  # set by client via set_instructions event
        agent_session=agent_session,
    )
    # Skip intro phase for resumed lessons that already have conversation history.
    if messages:
        state.phase = "teaching"

    async def _auto_start() -> None:
        """Run the intro turn (overview + goal question) for a fresh lesson."""
        import uuid as _uuid
        turn_id = str(_uuid.uuid4())
        state.last_turn_id = turn_id
        state.turn_status = "running"
        log.info("[auto-start] launching intro turn for lesson %s", lesson_id)
        try:
            # Run intro with a throwaway message list so state.messages stays clean.
            intro_messages: list[dict] = []
            await state.agent_session.run_intro(state.curriculum, intro_messages)
            state.turn_status = "complete"
            await websocket.send_json({"event": "turn_complete", "turn_id": turn_id})
        except asyncio.CancelledError:
            state.turn_status = "failed"
        except Exception as exc:
            log.exception("[auto-start] intro turn raised")
            state.turn_status = "failed"
            await websocket.send_json({"event": "error", "message": str(exc)})

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
            state.agent_session.handle_tool_result(inv_id, msg.get("result", {}))

        elif event == "set_instructions":
            state.agent_instructions = msg.get("instructions") or None

        elif event == "set_voice":
            voice = msg.get("voice")
            if voice:
                state.agent_session.agent.kokoro_voice = voice

        elif event == "set_stt_language":
            lang = msg.get("language")  # BCP-47 code or None / "" for auto
            state.stt_language = lang or None

        elif event == "reconnect":
            await _handle_reconnect(websocket, msg, state)

        elif event == "cancel_turn":
            if state.agent_task and not state.agent_task.done():
                state.agent_task.cancel()
                state.turn_status = "failed"

        elif event == "start_lesson":
            # Client sends this after dispatching initial config (set_instructions etc.)
            if (auto_start and not state.messages
                    and not (state.agent_task and not state.agent_task.done())):
                state.agent_task = asyncio.create_task(auto_start())

        elif event == "image_input":
            await _handle_image_input(websocket, msg, state, conn)

        elif event == "ping":
            await websocket.send_json({"event": "pong"})


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

        # When decomposition finishes, refresh the in-memory curriculum so the
        # teaching agent has the correct sections, then kick off the first turn.
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
            if not state.messages and not (state.agent_task and not state.agent_task.done()):
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
        text = await transcribe(audio_b64, sample_rate, app_state.stt_model, state.stt_language)
    except Exception as exc:
        await websocket.send_json({"event": "error", "message": f"STT error: {exc}"})
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

    # ── Intro phase: student's response IS the lesson goal ──────────────────
    if state.phase == "intro":
        state.lesson_goal = text
        state.phase = "teaching"
        # state.messages stays empty; teaching starts fresh with goal injected.
        await websocket.send_json({"event": "status", "message": "Thinking..."})

        async def _first_teaching_turn() -> None:
            log.info("[turn %s] first teaching turn (goal captured)", turn_id)
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
                log.exception("[turn %s] first teaching turn raised", turn_id)
                state.turn_status = "failed"
                await websocket.send_json({"event": "error", "message": str(exc)})

        state.agent_task = asyncio.create_task(_first_teaching_turn())
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
            await websocket.send_json({"event": "error", "message": str(exc)})

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
            await websocket.send_json({"event": "error", "message": str(exc)})

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
