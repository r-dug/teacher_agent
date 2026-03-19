"""Admin course publication: mark a decomposed course and its lessons as published."""

from __future__ import annotations

from typing import Any

import aiosqlite

from ...db import models


class CoursePublishPreconditionError(RuntimeError):
    """Raised when publication is requested before any lesson is decomposed."""


async def publish_course_to_all_users(
    conn: aiosqlite.Connection,
    *,
    source_course_id: str,
    actor_user_id: str,
) -> dict[str, Any]:
    """
    Publish an admin-authored course by setting visibility='published' on the
    course and all of its decomposed lessons.

    Only lessons with already-decomposed sections are published; undecomposed
    lessons stay as 'draft'.  Raises CoursePublishPreconditionError if no
    lesson is ready.
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

    # Publish the course itself
    await models.update_course(conn, source_course_id, visibility="published")

    # Publish each decomposed lesson
    for lesson in decomposed_lessons:
        await models.update_lesson(conn, lesson["id"], visibility="published")

    await conn.commit()

    return {
        "source_course_id": source_course_id,
        "target_users": 0,
        "published_courses": 1,
        "published_lessons": len(decomposed_lessons),
        "skipped_lessons": len(skipped_lessons),
        "skipped_lesson_titles": [str(l.get("title") or "") for l in skipped_lessons],
        "message": (
            "Only lessons with completed decomposition were published."
            if skipped_lessons
            else "Published successfully."
        ),
    }
