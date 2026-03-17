"""Admin course publication: clone a decomposed course to all non-admin users."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

import aiosqlite

from ..config import settings
from ..db import models


class CoursePublishPreconditionError(RuntimeError):
    """Raised when publication is requested before any lesson is decomposed."""


async def publish_course_to_all_users(
    conn: aiosqlite.Connection,
    *,
    source_course_id: str,
    actor_user_id: str,
) -> dict[str, Any]:
    """
    Clone an admin-owned source course to all non-admin users.

    Only lessons with already-decomposed sections are published. This avoids
    triggering expensive decomposition work on publish and removes onboarding
    wait time for end users.
    """
    source_course = await models.get_course(conn, source_course_id)
    if source_course is None:
        raise ValueError("Course not found")

    source_lessons = await models.list_lessons(
        conn,
        actor_user_id,
        limit=2000,
        offset=0,
        course_id=source_course_id,
    )
    decomposed_lessons = [l for l in source_lessons if int(l.get("section_count") or 0) > 0]
    skipped_lessons = [l for l in source_lessons if int(l.get("section_count") or 0) <= 0]
    if not decomposed_lessons:
        raise CoursePublishPreconditionError(
            "No decomposed lessons are ready yet. Run decomposition before publishing."
        )

    source_sections: dict[str, list[dict]] = {}
    for lesson in decomposed_lessons:
        source_sections[lesson["id"]] = await models.get_sections(conn, lesson["id"])

    target_user_ids = await _list_non_admin_user_ids(conn, exclude_user_id=actor_user_id)
    published_courses = 0
    published_lessons = 0

    for target_user_id in target_user_ids:
        target_course_id = await _ensure_target_course(
            conn=conn,
            source_course_id=source_course_id,
            target_user_id=target_user_id,
            source_title=source_course["title"],
            source_description=source_course.get("description"),
        )
        published_courses += 1

        existing_map = await _get_existing_lesson_map(
            conn=conn,
            source_course_id=source_course_id,
            source_owner_id=actor_user_id,
            target_user_id=target_user_id,
        )
        decomposed_ids = {lesson["id"] for lesson in decomposed_lessons}

        # Remove stale previously-published lessons no longer eligible.
        for source_lesson_id, target_lesson_id in existing_map.items():
            if source_lesson_id in decomposed_ids:
                continue
            await models.delete_lesson(conn, target_lesson_id)
            await conn.execute(
                "DELETE FROM lesson_publish_copies WHERE source_lesson_id = ? AND target_user_id = ?",
                (source_lesson_id, target_user_id),
            )

        for lesson in decomposed_lessons:
            source_lesson_id = lesson["id"]
            target_lesson_id = existing_map.get(source_lesson_id)
            target_lesson = (
                await models.get_lesson(conn, target_lesson_id) if target_lesson_id else None
            )
            if target_lesson is None or target_lesson["user_id"] != target_user_id:
                target_lesson_id = await models.create_lesson(
                    conn,
                    user_id=target_user_id,
                    title=lesson["title"],
                    pdf_path=None,
                    course_id=target_course_id,
                    description=lesson.get("description"),
                )
            else:
                await models.update_lesson(
                    conn,
                    target_lesson_id,
                    title=lesson["title"],
                    description=lesson.get("description"),
                    course_id=target_course_id,
                )

            copied_pdf_rel = await _copy_source_pdf_to_target(
                source_pdf_rel=lesson.get("pdf_path"),
                target_user_id=target_user_id,
                target_lesson_id=target_lesson_id,
            )
            await models.update_lesson(
                conn,
                target_lesson_id,
                pdf_path=copied_pdf_rel,
                current_section_idx=0,
                completed=0,
                lesson_goal=None,
            )
            await models.upsert_sections(conn, target_lesson_id, source_sections[source_lesson_id])
            await models.upsert_messages(conn, target_lesson_id, [])
            await conn.execute(
                """INSERT INTO lesson_publish_copies
                   (source_lesson_id, target_user_id, target_lesson_id, created_at, updated_at)
                   VALUES (?, ?, ?, datetime('now'), datetime('now'))
                   ON CONFLICT(source_lesson_id, target_user_id)
                   DO UPDATE SET
                     target_lesson_id = excluded.target_lesson_id,
                     updated_at = datetime('now')""",
                (source_lesson_id, target_user_id, target_lesson_id),
            )
            published_lessons += 1

    await conn.commit()
    return {
        "source_course_id": source_course_id,
        "target_users": len(target_user_ids),
        "published_courses": published_courses,
        "published_lessons": published_lessons,
        "skipped_lessons": len(skipped_lessons),
        "skipped_lesson_titles": [str(l.get("title") or "") for l in skipped_lessons],
        "message": (
            "Only lessons with completed decomposition were published."
            if skipped_lessons
            else "Published successfully."
        ),
    }


async def _list_non_admin_user_ids(
    conn: aiosqlite.Connection,
    *,
    exclude_user_id: str,
) -> list[str]:
    async with conn.execute(
        """SELECT id FROM users
           WHERE is_admin = 0
             AND id != ?
             AND email IS NOT NULL
           ORDER BY created_at""",
        (exclude_user_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [str(r[0]) for r in rows]


async def _ensure_target_course(
    *,
    conn: aiosqlite.Connection,
    source_course_id: str,
    target_user_id: str,
    source_title: str,
    source_description: str | None,
) -> str:
    async with conn.execute(
        """SELECT target_course_id
           FROM course_publish_copies
           WHERE source_course_id = ? AND target_user_id = ?""",
        (source_course_id, target_user_id),
    ) as cur:
        row = await cur.fetchone()

    target_course_id = str(row[0]) if row else ""
    target_course = await models.get_course(conn, target_course_id) if target_course_id else None
    if target_course is None or target_course["user_id"] != target_user_id:
        target_course = await models.create_course(
            conn,
            user_id=target_user_id,
            title=source_title,
            description=source_description,
        )
        target_course_id = target_course["id"]
    else:
        await models.update_course(
            conn,
            target_course_id,
            title=source_title,
            description=source_description,
        )

    await conn.execute(
        """INSERT INTO course_publish_copies
           (source_course_id, target_user_id, target_course_id, created_at, updated_at)
           VALUES (?, ?, ?, datetime('now'), datetime('now'))
           ON CONFLICT(source_course_id, target_user_id)
           DO UPDATE SET
             target_course_id = excluded.target_course_id,
             updated_at = datetime('now')""",
        (source_course_id, target_user_id, target_course_id),
    )
    return target_course_id


async def _get_existing_lesson_map(
    *,
    conn: aiosqlite.Connection,
    source_course_id: str,
    source_owner_id: str,
    target_user_id: str,
) -> dict[str, str]:
    async with conn.execute(
        """SELECT lpc.source_lesson_id, lpc.target_lesson_id
           FROM lesson_publish_copies lpc
           JOIN lessons src ON src.id = lpc.source_lesson_id
           WHERE src.course_id = ?
             AND src.user_id = ?
             AND lpc.target_user_id = ?""",
        (source_course_id, source_owner_id, target_user_id),
    ) as cur:
        rows = await cur.fetchall()
    return {str(r[0]): str(r[1]) for r in rows}


async def _copy_source_pdf_to_target(
    *,
    source_pdf_rel: str | None,
    target_user_id: str,
    target_lesson_id: str,
) -> str | None:
    if not source_pdf_rel:
        return None
    src = (settings.STORAGE_DIR / source_pdf_rel).resolve()
    storage_root = settings.STORAGE_DIR.resolve()
    try:
        if not src.is_relative_to(storage_root):
            return None
    except Exception:
        return None
    if not src.exists():
        return None

    target_rel = f"{target_user_id}/pdfs/{target_lesson_id}.pdf"
    dst = settings.STORAGE_DIR / Path(target_rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(shutil.copy2, src, dst)
    return target_rel
