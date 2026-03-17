"""Course CRUD endpoints."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..config import settings
from ..db import connection as db, models
from ..services.course_publish import (
    CoursePublishPreconditionError,
    publish_course_to_all_users,
)
from ..services.course_authoring import (
    advisor_user_message,
    create_decomposition_job,
    ensure_advisor_session,
    finalize_advisor_session,
    get_job_status as get_course_decompose_status,
    launch_decomposition_job,
)
from ..services.textbook_authoring import (
    infer_chapter_drafts_from_pdf,
    normalize_chapter_drafts,
    sha256_file,
)

router = APIRouter(prefix="/courses", tags=["courses"])

Conn = Annotated[aiosqlite.Connection, Depends(db.get)]


# ── Pydantic models ────────────────────────────────────────────────────────────

class CourseResponse(BaseModel):
    id: str
    user_id: str
    title: str
    description: str | None
    created_at: str
    updated_at: str


class CourseCreate(BaseModel):
    title: str
    description: str | None = None


class CourseUpdate(BaseModel):
    title: str | None = None
    description: str | None = None


class CoursePublishResponse(BaseModel):
    source_course_id: str
    target_users: int
    published_courses: int
    published_lessons: int
    skipped_lessons: int
    skipped_lesson_titles: list[str]
    message: str


class ChapterDraftResponse(BaseModel):
    id: str
    idx: int
    title: str
    page_start: int
    page_end: int
    included: bool


class TextbookSourceResponse(BaseModel):
    pdf_hash: str
    pdf_path: str
    page_count: int
    cache_hit: bool


class TextbookDraftResponse(BaseModel):
    course: CourseResponse
    source: TextbookSourceResponse
    chapters: list[ChapterDraftResponse]


class ChapterEditItem(BaseModel):
    id: str | None = None
    title: str
    page_start: int
    page_end: int
    included: bool = True


class ChapterEditRequest(BaseModel):
    chapters: list[ChapterEditItem]


class ChapterDraftListResponse(BaseModel):
    course_id: str
    pdf_hash: str
    page_count: int
    chapters: list[ChapterDraftResponse]


class AdvisorStartRequest(BaseModel):
    reset: bool = False


class AdvisorMessageRequest(BaseModel):
    text: str


class AdvisorResponse(BaseModel):
    course_id: str
    status: str
    transcript: list[dict]
    assistant: str | None = None
    objectives_prompt: str | None = None


class DecomposeStartRequest(BaseModel):
    notify_session_id: str | None = None
    objectives_prompt: str | None = None


class DecomposeJobItemResponse(BaseModel):
    id: str
    chapter_id: str
    idx: int
    title: str
    page_start: int
    page_end: int
    lesson_id: str | None = None
    cache_key: str | None = None
    status: str
    error: str | None = None


class DecomposeJobResponse(BaseModel):
    id: str
    course_id: str
    user_id: str
    status: str
    objectives_prompt: str
    total_items: int
    completed_items: int
    failed_items: int
    progress_pct: int = 0
    notify_session_id: str | None = None
    error: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    updated_at: str


class DecomposeStatusResponse(BaseModel):
    job: DecomposeJobResponse | None
    items: list[DecomposeJobItemResponse]


# ── helpers ────────────────────────────────────────────────────────────────────

def _course_or_404(course: dict | None) -> dict:
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


def _check_ownership(course: dict, user_id: str) -> None:
    if course["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Access denied")


async def _require_admin(conn: aiosqlite.Connection, user_id: str) -> None:
    if not await models.get_user_is_admin(conn, user_id):
        raise HTTPException(status_code=403, detail="Admin access required")


async def _source_or_404(conn: aiosqlite.Connection, course_id: str) -> dict:
    async with conn.execute(
        """SELECT course_id, user_id, pdf_hash, pdf_path, page_count, toc_json
           FROM course_source_files
           WHERE course_id = ?""",
        (course_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No textbook source found for this course")
    return dict(row)


async def _load_chapter_rows(
    conn: aiosqlite.Connection, course_id: str
) -> list[dict]:
    async with conn.execute(
        """SELECT id, idx, title, page_start, page_end, included
           FROM course_chapter_drafts
           WHERE course_id = ?
           ORDER BY idx""",
        (course_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _replace_chapters(
    conn: aiosqlite.Connection,
    *,
    course_id: str,
    chapters: list[dict],
) -> None:
    await conn.execute(
        "DELETE FROM course_chapter_drafts WHERE course_id = ?",
        (course_id,),
    )
    for idx, chapter in enumerate(chapters):
        cid = chapter.get("id") or db.new_id()
        await conn.execute(
            """INSERT INTO course_chapter_drafts
               (id, course_id, idx, title, page_start, page_end, included, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                cid,
                course_id,
                idx,
                str(chapter["title"]),
                int(chapter["page_start"]),
                int(chapter["page_end"]),
                int(bool(chapter.get("included", True))),
            ),
        )


def _course_source_path(user_id: str, course_id: str) -> Path:
    return settings.STORAGE_DIR / user_id / "course_sources" / f"{course_id}.pdf"


# ── routes ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[CourseResponse])
async def list_courses(user_id: str, conn: Conn):
    rows = await models.list_courses(conn, user_id)
    return [CourseResponse(**r) for r in rows]


@router.post("", response_model=CourseResponse, status_code=201)
async def create_course(user_id: str, body: CourseCreate, conn: Conn):
    await _require_admin(conn, user_id)
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Title is required")
    course = await models.create_course(conn, user_id, title, body.description)
    return CourseResponse(**course)


@router.get("/{course_id}", response_model=CourseResponse)
async def get_course(course_id: str, user_id: str, conn: Conn):
    course = _course_or_404(await models.get_course(conn, course_id))
    _check_ownership(course, user_id)
    return CourseResponse(**course)


@router.patch("/{course_id}", response_model=CourseResponse)
async def update_course(course_id: str, user_id: str, body: CourseUpdate, conn: Conn):
    course = _course_or_404(await models.get_course(conn, course_id))
    _check_ownership(course, user_id)
    updates: dict = {}
    if body.title is not None:
        updates["title"] = body.title.strip() or course["title"]
    if body.description is not None:
        updates["description"] = body.description
    if updates:
        await models.update_course(conn, course_id, **updates)
    course = await models.get_course(conn, course_id)
    return CourseResponse(**course)  # type: ignore[arg-type]


@router.delete("/{course_id}", status_code=204)
async def delete_course(
    course_id: str,
    user_id: str,
    conn: Conn,
    cascade_lessons: bool = False,
):
    course = _course_or_404(await models.get_course(conn, course_id))
    _check_ownership(course, user_id)
    if cascade_lessons:
        await conn.execute(
            "DELETE FROM lessons WHERE course_id = ? AND user_id = ?",
            (course_id, user_id),
        )
        await conn.commit()
    await models.delete_course(conn, course_id)


@router.post("/{course_id}/publish", response_model=CoursePublishResponse)
async def publish_course(course_id: str, user_id: str, conn: Conn):
    await _require_admin(conn, user_id)

    course = _course_or_404(await models.get_course(conn, course_id))
    _check_ownership(course, user_id)

    try:
        result = await publish_course_to_all_users(
            conn,
            source_course_id=course_id,
            actor_user_id=user_id,
        )
    except CoursePublishPreconditionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CoursePublishResponse(**result)


@router.post("/textbook/draft", response_model=TextbookDraftResponse, status_code=201)
async def create_textbook_draft(
    user_id: str,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    description: str | None = Form(None),
    conn: Conn = None,
):
    await _require_admin(conn, user_id)

    course_title = (title or "").strip() or Path(file.filename or "Untitled Textbook").stem
    if not course_title:
        raise HTTPException(status_code=422, detail="Course title is required")

    course = await models.create_course(conn, user_id, course_title, description)
    course_id = course["id"]
    pdf_path = _course_source_path(user_id, course_id)
    try:
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        with pdf_path.open("wb") as f:
            shutil.copyfileobj(file.file, f)

        pdf_hash = sha256_file(pdf_path)
        cache_hit = False
        async with conn.execute(
            "SELECT page_count, toc_json, chapters_json FROM textbook_toc_cache WHERE pdf_hash = ?",
            (pdf_hash,),
        ) as cur:
            cache_row = await cur.fetchone()

        if cache_row is None:
            page_count, toc_entries, chapter_drafts = infer_chapter_drafts_from_pdf(pdf_path)
            await conn.execute(
                """INSERT INTO textbook_toc_cache
                   (pdf_hash, page_count, toc_json, chapters_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
                   ON CONFLICT(pdf_hash) DO UPDATE SET
                     page_count = excluded.page_count,
                     toc_json = excluded.toc_json,
                     chapters_json = excluded.chapters_json,
                     updated_at = datetime('now')""",
                (
                    pdf_hash,
                    page_count,
                    json.dumps(toc_entries),
                    json.dumps(chapter_drafts),
                ),
            )
        else:
            cache_hit = True
            page_count = int(cache_row[0])
            toc_entries = json.loads(cache_row[1] or "[]")
            chapter_drafts = json.loads(cache_row[2] or "[]")

        rel_path = str(pdf_path.relative_to(settings.STORAGE_DIR))
        await conn.execute(
            """INSERT INTO course_source_files
               (course_id, user_id, pdf_hash, pdf_path, page_count, toc_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
               ON CONFLICT(course_id) DO UPDATE SET
                 user_id = excluded.user_id,
                 pdf_hash = excluded.pdf_hash,
                 pdf_path = excluded.pdf_path,
                 page_count = excluded.page_count,
                 toc_json = excluded.toc_json,
                 updated_at = datetime('now')""",
            (
                course_id,
                user_id,
                pdf_hash,
                rel_path,
                page_count,
                json.dumps(toc_entries),
            ),
        )
        await _replace_chapters(conn, course_id=course_id, chapters=chapter_drafts)
        await conn.commit()
    except HTTPException:
        await models.delete_course(conn, course_id)
        if pdf_path.exists():
            pdf_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        await models.delete_course(conn, course_id)
        if pdf_path.exists():
            pdf_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"Failed to parse textbook PDF: {exc}") from exc

    chapter_rows = await _load_chapter_rows(conn, course_id)
    return TextbookDraftResponse(
        course=CourseResponse(**course),
        source=TextbookSourceResponse(
            pdf_hash=pdf_hash,
            pdf_path=rel_path,
            page_count=page_count,
            cache_hit=cache_hit,
        ),
        chapters=[
            ChapterDraftResponse(
                id=str(r["id"]),
                idx=int(r["idx"]),
                title=str(r["title"]),
                page_start=int(r["page_start"]),
                page_end=int(r["page_end"]),
                included=bool(r["included"]),
            )
            for r in chapter_rows
        ],
    )


@router.get("/{course_id}/chapters", response_model=ChapterDraftListResponse)
async def get_course_chapters(course_id: str, user_id: str, conn: Conn):
    await _require_admin(conn, user_id)
    course = _course_or_404(await models.get_course(conn, course_id))
    _check_ownership(course, user_id)
    source = await _source_or_404(conn, course_id)
    rows = await _load_chapter_rows(conn, course_id)
    return ChapterDraftListResponse(
        course_id=course_id,
        pdf_hash=str(source["pdf_hash"]),
        page_count=int(source["page_count"]),
        chapters=[
            ChapterDraftResponse(
                id=str(r["id"]),
                idx=int(r["idx"]),
                title=str(r["title"]),
                page_start=int(r["page_start"]),
                page_end=int(r["page_end"]),
                included=bool(r["included"]),
            )
            for r in rows
        ],
    )


@router.patch("/{course_id}/chapters", response_model=ChapterDraftListResponse)
async def update_course_chapters(
    course_id: str,
    user_id: str,
    body: ChapterEditRequest,
    conn: Conn,
):
    await _require_admin(conn, user_id)
    course = _course_or_404(await models.get_course(conn, course_id))
    _check_ownership(course, user_id)
    source = await _source_or_404(conn, course_id)
    if not body.chapters:
        raise HTTPException(status_code=422, detail="At least one chapter is required")

    try:
        normalized = normalize_chapter_drafts(
            [c.model_dump() for c in body.chapters],
            page_count=int(source["page_count"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    drafted: list[dict] = []
    for idx, chapter in enumerate(normalized):
        existing_id = body.chapters[idx].id if idx < len(body.chapters) else None
        drafted.append(
            {
                "id": existing_id,
                "title": chapter["title"],
                "page_start": chapter["page_start"],
                "page_end": chapter["page_end"],
                "included": chapter["included"],
            }
        )
    await _replace_chapters(conn, course_id=course_id, chapters=drafted)
    await conn.commit()

    rows = await _load_chapter_rows(conn, course_id)
    return ChapterDraftListResponse(
        course_id=course_id,
        pdf_hash=str(source["pdf_hash"]),
        page_count=int(source["page_count"]),
        chapters=[
            ChapterDraftResponse(
                id=str(r["id"]),
                idx=int(r["idx"]),
                title=str(r["title"]),
                page_start=int(r["page_start"]),
                page_end=int(r["page_end"]),
                included=bool(r["included"]),
            )
            for r in rows
        ],
    )


@router.post("/{course_id}/advisor/start", response_model=AdvisorResponse)
async def advisor_start(
    course_id: str,
    user_id: str,
    body: AdvisorStartRequest,
    conn: Conn,
):
    await _require_admin(conn, user_id)
    course = _course_or_404(await models.get_course(conn, course_id))
    _check_ownership(course, user_id)
    _ = await _source_or_404(conn, course_id)
    state = await ensure_advisor_session(conn, course_id=course_id, user_id=user_id, reset=body.reset)
    return AdvisorResponse(**state)


@router.post("/{course_id}/advisor/message", response_model=AdvisorResponse)
async def advisor_message(
    course_id: str,
    user_id: str,
    body: AdvisorMessageRequest,
    conn: Conn,
):
    await _require_admin(conn, user_id)
    course = _course_or_404(await models.get_course(conn, course_id))
    _check_ownership(course, user_id)
    _ = await _source_or_404(conn, course_id)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Message text is required")
    state = await advisor_user_message(conn, course_id=course_id, user_id=user_id, text=text)
    return AdvisorResponse(**state)


@router.post("/{course_id}/advisor/finalize", response_model=AdvisorResponse)
async def advisor_finalize(course_id: str, user_id: str, conn: Conn):
    await _require_admin(conn, user_id)
    course = _course_or_404(await models.get_course(conn, course_id))
    _check_ownership(course, user_id)
    _ = await _source_or_404(conn, course_id)
    state = await finalize_advisor_session(conn, course_id=course_id, user_id=user_id)
    return AdvisorResponse(**state)


@router.post("/{course_id}/decompose/start", response_model=DecomposeStatusResponse)
async def start_course_decompose(
    course_id: str,
    user_id: str,
    body: DecomposeStartRequest,
    conn: Conn,
):
    await _require_admin(conn, user_id)
    course = _course_or_404(await models.get_course(conn, course_id))
    _check_ownership(course, user_id)
    _ = await _source_or_404(conn, course_id)

    try:
        status = await create_decomposition_job(
            conn,
            course_id=course_id,
            user_id=user_id,
            notify_session_id=(body.notify_session_id or "").strip() or None,
            objectives_prompt_override=(body.objectives_prompt or "").strip() or None,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    job = status.get("job") or {}
    job_id = str(job.get("id") or "")
    if job_id:
        launch_decomposition_job(job_id)
    return DecomposeStatusResponse(**status)


@router.get("/{course_id}/decompose/status", response_model=DecomposeStatusResponse)
async def course_decompose_status(
    course_id: str,
    user_id: str,
    conn: Conn,
    job_id: str | None = None,
):
    await _require_admin(conn, user_id)
    course = _course_or_404(await models.get_course(conn, course_id))
    _check_ownership(course, user_id)
    status = await get_course_decompose_status(conn, course_id=course_id, job_id=job_id)
    return DecomposeStatusResponse(**status)
