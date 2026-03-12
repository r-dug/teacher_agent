"""Lesson CRUD and PDF decomposition endpoints."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

from ..app_state import app_state, registry
from ..config import settings
from ..db import connection as db, models
from ..services.agent import BackendAgentSession

router = APIRouter(prefix="/lessons", tags=["lessons"])

Conn = Annotated[aiosqlite.Connection, Depends(db.get)]


# ── PDF page image ─────────────────────────────────────────────────────────────

@router.get("/{lesson_id}/page/{page_number}")
async def get_lesson_page(
    lesson_id: str,
    page_number: int,
    conn: Annotated[aiosqlite.Connection, Depends(db.get)],
):
    """
    Render a single PDF page as a PNG image.

    page_number is 1-based.  Returns the image as a streaming response so the
    frontend/client can display it without downloading the full PDF.
    """
    import io
    import fitz  # pymupdf
    from fastapi.responses import Response

    lesson = await models.get_lesson(conn, lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    if not lesson.get("pdf_path"):
        raise HTTPException(status_code=404, detail="No PDF associated with this lesson")

    pdf_full_path = settings.STORAGE_DIR / lesson["pdf_path"]
    if not pdf_full_path.exists():
        raise HTTPException(status_code=404, detail="PDF file not found on disk")

    try:
        doc = fitz.open(str(pdf_full_path))
        if page_number < 1 or page_number > len(doc):
            raise HTTPException(
                status_code=400,
                detail=f"Page {page_number} out of range (1–{len(doc)})",
            )
        page = doc[page_number - 1]
        # 2× DPI for a crisp render without being enormous
        mat = fitz.Matrix(2.0, 2.0)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("png")
        doc.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF render error: {exc}")

    return Response(content=img_bytes, media_type="image/png")


# ── Pydantic models ────────────────────────────────────────────────────────────

class LessonResponse(BaseModel):
    id: str
    user_id: str
    title: str
    pdf_path: str | None
    current_section_idx: int
    completed: bool
    created_at: str
    updated_at: str


class LessonDetailResponse(LessonResponse):
    sections: list[dict]
    messages: list[dict]


class LessonUpdate(BaseModel):
    current_section_idx: int | None = None
    completed: bool | None = None


# ── helpers ────────────────────────────────────────────────────────────────────

def _pdf_storage_path(user_id: str, lesson_id: str) -> Path:
    return settings.STORAGE_DIR / user_id / "pdfs" / f"{lesson_id}.pdf"


def _lesson_or_404(lesson: dict | None) -> dict:
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    return lesson


# ── routes ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[LessonResponse])
async def list_lessons(user_id: str, conn: Conn):
    rows = await models.list_lessons(conn, user_id)
    return [LessonResponse(**{**r, "completed": bool(r["completed"])}) for r in rows]


@router.get("/{lesson_id}", response_model=LessonDetailResponse)
async def get_lesson(lesson_id: str, conn: Conn):
    lesson = _lesson_or_404(await models.get_lesson(conn, lesson_id))
    sections = await models.get_sections(conn, lesson_id)
    messages = await models.get_messages(conn, lesson_id)
    return LessonDetailResponse(
        **{**lesson, "completed": bool(lesson["completed"])},
        sections=sections,
        messages=messages,
    )


@router.patch("/{lesson_id}", response_model=LessonResponse)
async def update_lesson(lesson_id: str, body: LessonUpdate, conn: Conn):
    _lesson_or_404(await models.get_lesson(conn, lesson_id))
    updates: dict = {}
    if body.current_section_idx is not None:
        updates["current_section_idx"] = body.current_section_idx
    if body.completed is not None:
        updates["completed"] = int(body.completed)
    if updates:
        await models.update_lesson(conn, lesson_id, **updates)
    lesson = await models.get_lesson(conn, lesson_id)
    return LessonResponse(**{**lesson, "completed": bool(lesson["completed"])})


@router.delete("/{lesson_id}", status_code=204)
async def delete_lesson(lesson_id: str, conn: Conn):
    lesson = _lesson_or_404(await models.get_lesson(conn, lesson_id))
    # Remove PDF file if present
    if lesson.get("pdf_path"):
        full = settings.STORAGE_DIR / lesson["pdf_path"]
        full.unlink(missing_ok=True)
    await models.delete_lesson(conn, lesson_id)


@router.post("/save/{lesson_id}", status_code=204)
async def save_lesson_state(
    lesson_id: str,
    body: dict,
    conn: Conn,
):
    """
    Persist lesson state sent by the WS session handler after each turn.
    Body: {current_section_idx, completed, sections (optional), messages}.
    """
    _lesson_or_404(await models.get_lesson(conn, lesson_id))
    await models.update_lesson(
        conn,
        lesson_id,
        current_section_idx=body.get("current_section_idx", 0),
        completed=int(body.get("completed", False)),
    )
    if sections := body.get("sections"):
        await models.upsert_sections(conn, lesson_id, sections)
    if messages := body.get("messages"):
        await models.upsert_messages(conn, lesson_id, messages)


# ── PDF decomposition ──────────────────────────────────────────────────────────

class DecomposeResponse(BaseModel):
    lesson_id: str
    status: str = "decomposing"


@router.post("/decompose", response_model=DecomposeResponse)
async def decompose_pdf(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    x_upload_token: str = Header(..., alias="X-Upload-Token"),
    conn: Conn = None,
):
    """
    Accept a PDF upload and kick off background decomposition.

    The client must include the short-lived upload token issued by the frontend
    server as the X-Upload-Token header.  Progress and completion are streamed
    over the client's active WebSocket session.
    """
    # Validate upload token
    session = await models.consume_upload_token(conn, x_upload_token)
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid or expired upload token")

    user_id: str = session["user_id"]

    # Create lesson record (title derived from filename)
    title = Path(file.filename or "Untitled").stem
    lesson_id = await models.create_lesson(conn, user_id, title)

    # Save PDF to storage
    pdf_path = _pdf_storage_path(user_id, lesson_id)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    with pdf_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    # Store relative path in DB
    rel_path = str(pdf_path.relative_to(settings.STORAGE_DIR))
    await models.update_lesson(conn, lesson_id, pdf_path=rel_path)

    # Run decomposition in the background
    loop = asyncio.get_event_loop()
    asyncio.create_task(
        _decompose_background(session_id, lesson_id, str(pdf_path), loop, conn)
    )

    return DecomposeResponse(lesson_id=lesson_id)


async def _decompose_background(
    session_id: str,
    lesson_id: str,
    pdf_path: str,
    loop: asyncio.AbstractEventLoop,
    conn: aiosqlite.Connection,
) -> None:
    """Background task: decompose PDF and stream progress events to the session."""

    def on_progress(msg: str) -> None:
        registry.send_threadsafe(session_id, {"event": "status", "message": msg}, loop)

    try:
        agent_session = BackendAgentSession(
            send=lambda e: registry.send(session_id, e),
            loop=loop,
            kokoro_pipeline=None,  # not needed for decomposition
            llm_model=settings.LLM_MODEL,
        )
        curriculum = await agent_session.decompose_pdf(pdf_path, on_progress)

        # Persist sections
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
        await registry.send(session_id, {
            "event": "error",
            "message": f"Decomposition failed: {exc}",
        })
