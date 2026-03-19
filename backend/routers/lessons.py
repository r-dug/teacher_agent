"""Lesson CRUD and PDF decomposition endpoints."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

from ..config import settings
from ..db import connection as db, models

router = APIRouter(prefix="/lessons", tags=["lessons"])

Conn = Annotated[aiosqlite.Connection, Depends(db.get)]


# ── PDF page image ─────────────────────────────────────────────────────────────

@router.get("/{lesson_id}/page/{page_number}")
async def get_lesson_page(
    lesson_id: str,
    page_number: int,
    user_id: str,
    conn: Annotated[aiosqlite.Connection, Depends(db.get)],
):
    """
    Render a single PDF page as a PNG image.

    page_number is 1-based.  Returns the image as a streaming response so the
    frontend/client can display it without downloading the full PDF.
    """
    import fitz  # pymupdf
    from fastapi.responses import Response

    lesson = await models.get_lesson(conn, lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    _check_access(lesson, user_id)
    if not lesson.get("pdf_path"):
        raise HTTPException(status_code=404, detail="No PDF associated with this lesson")

    pdf_full_path = settings.STORAGE_DIR / lesson["pdf_path"]
    _validate_pdf_path(pdf_full_path)
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
    creator_id: str
    course_id: str | None
    title: str
    description: str | None
    pdf_path: str | None
    visibility: str
    current_section_idx: int
    completed: bool
    section_count: int = 0
    created_at: str
    updated_at: str


class LessonDetailResponse(LessonResponse):
    sections: list[dict]
    messages: list[dict]


class LessonUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    visibility: str | None = None
    # course_id uses model_fields_set so explicit null (remove from course) is detectable
    course_id: str | None = None


# ── helpers ────────────────────────────────────────────────────────────────────

def _lesson_pdf_path(lesson_id: str) -> Path:
    """New storage layout: STORAGE_DIR/lessons/{lesson_id}.pdf"""
    return settings.STORAGE_DIR / "lessons" / f"{lesson_id}.pdf"


def _lesson_or_404(lesson: dict | None) -> dict:
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    return lesson


def _check_access(lesson: dict, user_id: str) -> None:
    """Allow if user is creator OR lesson is published."""
    if lesson["creator_id"] != user_id and lesson.get("visibility") != "published":
        raise HTTPException(status_code=403, detail="Access denied")


def _check_creator(lesson: dict, user_id: str) -> None:
    """Allow only the creator (for write operations)."""
    if lesson["creator_id"] != user_id:
        raise HTTPException(status_code=403, detail="Access denied")


def _validate_pdf_path(pdf_full_path: Path) -> None:
    """Raise 403 if pdf_full_path escapes STORAGE_DIR (path traversal guard)."""
    try:
        real_path = pdf_full_path.resolve()
        real_storage = settings.STORAGE_DIR.resolve()
        if not real_path.is_relative_to(real_storage):
            raise HTTPException(status_code=403, detail="Access denied")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=403, detail="Access denied")


def _lesson_to_response(lesson: dict, section_count: int = 0) -> LessonResponse:
    return LessonResponse(
        **{
            **lesson,
            "current_section_idx": lesson.get("current_section_idx") or 0,
            "completed": bool(lesson.get("completed", 0)),
            "section_count": section_count,
        }
    )


# ── routes ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[LessonResponse])
async def list_lessons(
    user_id: str,
    conn: Conn,
    limit: int = 50,
    offset: int = 0,
    course_id: str | None = None,
    standalone: bool = False,
):
    limit = max(1, min(limit, 200))
    rows = await models.list_lessons(
        conn, user_id, limit=limit, offset=offset,
        course_id=course_id, standalone=standalone,
    )
    return [_lesson_to_response(r, r.get("section_count", 0)) for r in rows]


@router.get("/{lesson_id}", response_model=LessonDetailResponse)
async def get_lesson(lesson_id: str, user_id: str, conn: Conn):
    lesson = _lesson_or_404(await models.get_lesson(conn, lesson_id))
    _check_access(lesson, user_id)
    sections = await models.get_sections(conn, lesson_id)
    # Load messages via enrollment
    enrollment = await models.get_enrollment(conn, lesson_id, user_id)
    messages = await models.get_messages(conn, enrollment["id"]) if enrollment else []
    enrollment_idx = enrollment["current_section_idx"] if enrollment else 0
    enrollment_completed = bool(enrollment["completed"]) if enrollment else False
    return LessonDetailResponse(
        **{
            **lesson,
            "current_section_idx": enrollment_idx,
            "completed": enrollment_completed,
            "section_count": len(sections),
        },
        sections=sections,
        messages=messages,
    )


@router.patch("/{lesson_id}", response_model=LessonResponse)
async def update_lesson(lesson_id: str, user_id: str, body: LessonUpdate, conn: Conn):
    lesson = _lesson_or_404(await models.get_lesson(conn, lesson_id))
    _check_creator(lesson, user_id)
    updates: dict = {}
    if body.title is not None:
        updates["title"] = body.title.strip() or lesson["title"]
    if body.description is not None:
        updates["description"] = body.description
    if body.visibility is not None:
        if body.visibility not in ("draft", "published"):
            raise HTTPException(status_code=422, detail="visibility must be 'draft' or 'published'")
        updates["visibility"] = body.visibility
    if "course_id" in body.model_fields_set:
        updates["course_id"] = body.course_id  # can be None to remove from course
    if updates:
        await models.update_lesson(conn, lesson_id, **updates)
    lesson = await models.get_lesson(conn, lesson_id)
    async with conn.execute(
        "SELECT COUNT(*) FROM lesson_sections WHERE lesson_id = ?", (lesson_id,)
    ) as cur:
        cnt_row = await cur.fetchone()
    section_count = cnt_row[0] if cnt_row else 0
    return _lesson_to_response(lesson, section_count)  # type: ignore[arg-type]


@router.delete("/{lesson_id}", status_code=204)
async def delete_lesson(lesson_id: str, user_id: str, conn: Conn):
    lesson = _lesson_or_404(await models.get_lesson(conn, lesson_id))
    _check_creator(lesson, user_id)
    # Remove PDF file if present
    if lesson.get("pdf_path"):
        full = settings.STORAGE_DIR / lesson["pdf_path"]
        full.unlink(missing_ok=True)
    await models.delete_lesson(conn, lesson_id)


@router.post("/save/{lesson_id}", status_code=204)
async def save_lesson_state(
    lesson_id: str,
    user_id: str,
    body: dict,
    conn: Conn,
):
    """
    Persist lesson state sent by the WS session handler after each turn.
    Body: {current_section_idx, completed, sections (optional), messages}.
    """
    lesson = _lesson_or_404(await models.get_lesson(conn, lesson_id))
    _check_access(lesson, user_id)
    enrollment = await models.get_or_create_enrollment(conn, lesson_id, user_id)
    await models.update_enrollment(
        conn,
        enrollment["id"],
        current_section_idx=body.get("current_section_idx", 0),
        completed=int(body.get("completed", False)),
    )
    if sections := body.get("sections"):
        await models.upsert_sections(conn, lesson_id, sections)
    if messages := body.get("messages"):
        await models.upsert_messages(conn, enrollment["id"], messages)


# ── PDF decomposition ──────────────────────────────────────────────────────────

class DecomposeResponse(BaseModel):
    lesson_id: str
    status: str = "decomposing"


@router.post("/decompose", response_model=DecomposeResponse)
async def decompose_pdf(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    x_upload_token: str = Header(..., alias="X-Upload-Token"),
    lesson_name: str | None = Form(None),
    description: str | None = Form(None),
    course_id: str | None = Form(None),
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
    if not await models.get_user_is_admin(conn, user_id):
        raise HTTPException(status_code=403, detail="Admin access required")

    # Derive title: prefer user-supplied name, fall back to filename stem
    title = (lesson_name.strip() if lesson_name and lesson_name.strip()
             else Path(file.filename or "Untitled").stem)
    lesson_id = await models.create_lesson(
        conn, user_id, title,
        course_id=course_id or None,
        description=description or None,
    )

    # Save PDF to new storage layout
    pdf_path = _lesson_pdf_path(lesson_id)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    with pdf_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    # Store relative path in DB
    rel_path = str(pdf_path.relative_to(settings.STORAGE_DIR))
    await models.update_lesson(conn, lesson_id, pdf_path=rel_path)

    # Decomposition is deferred: it runs after the intro conversation over WebSocket,
    # so the student's learning goal can inform how the curriculum is structured.
    return DecomposeResponse(lesson_id=lesson_id)
