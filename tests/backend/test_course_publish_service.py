"""Tests for admin course publication service (visibility-based model)."""

from __future__ import annotations

import pytest

from backend.db import models
from backend.services.documents.course_publish import (
    CoursePublishPreconditionError,
    publish_course_to_all_users,
)


@pytest.mark.asyncio
async def test_publish_sets_visibility_on_decomposed_lessons_only(mem_db):
    admin = await models.create_user(mem_db, "admin@example.com", "pw")
    await mem_db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin["id"],))
    student = await models.create_user(mem_db, "student@example.com", "pw")
    await mem_db.commit()

    course = await models.create_course(mem_db, admin["id"], "Starter JP", "desc")
    decomposed_id = await models.create_lesson(mem_db, admin["id"], "L1", course_id=course["id"])
    undecomposed_id = await models.create_lesson(mem_db, admin["id"], "L2", course_id=course["id"])
    await models.upsert_sections(
        mem_db,
        decomposed_id,
        [{"title": "s", "content": "c", "key_concepts": ["k"], "page_start": 1, "page_end": 1}],
    )

    result = await publish_course_to_all_users(
        mem_db,
        source_course_id=course["id"],
        actor_user_id=admin["id"],
    )

    assert result["published_courses"] == 1
    assert result["published_lessons"] == 1
    assert result["skipped_lessons"] == 1

    # Course is now published
    updated_course = await models.get_course(mem_db, course["id"])
    assert updated_course["visibility"] == "published"

    # Decomposed lesson is published; undecomposed stays draft
    decomposed = await models.get_lesson(mem_db, decomposed_id)
    assert decomposed["visibility"] == "published"
    undecomposed = await models.get_lesson(mem_db, undecomposed_id)
    assert undecomposed["visibility"] == "draft"

    # Student can see the published course and lesson via list queries
    student_courses = await models.list_courses(mem_db, student["id"])
    assert any(c["id"] == course["id"] for c in student_courses)

    student_lessons = await models.list_lessons(mem_db, student["id"], course_id=course["id"])
    assert len(student_lessons) == 1
    assert student_lessons[0]["id"] == decomposed_id


@pytest.mark.asyncio
async def test_publish_is_idempotent(mem_db):
    admin = await models.create_user(mem_db, "admin2@example.com", "pw")
    await mem_db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin["id"],))
    student = await models.create_user(mem_db, "student2@example.com", "pw")
    await mem_db.commit()

    course = await models.create_course(mem_db, admin["id"], "Course A", "desc A")
    lesson_id = await models.create_lesson(mem_db, admin["id"], "Lesson A", course_id=course["id"])
    await models.upsert_sections(
        mem_db,
        lesson_id,
        [{"title": "S1", "content": "C1", "key_concepts": []}],
    )

    first = await publish_course_to_all_users(mem_db, source_course_id=course["id"], actor_user_id=admin["id"])
    assert first["published_lessons"] == 1

    # Update source content and republish
    await models.update_course(mem_db, course["id"], title="Course A v2")
    await models.update_lesson(mem_db, lesson_id, title="Lesson A v2")

    second = await publish_course_to_all_users(mem_db, source_course_id=course["id"], actor_user_id=admin["id"])
    assert second["published_lessons"] == 1

    # Still one course and one lesson visible to student — no duplicates
    student_courses = await models.list_courses(mem_db, student["id"])
    assert sum(1 for c in student_courses if c["id"] == course["id"]) == 1

    student_lessons = await models.list_lessons(mem_db, student["id"], course_id=course["id"])
    assert len(student_lessons) == 1
    assert student_lessons[0]["title"] == "Lesson A v2"


@pytest.mark.asyncio
async def test_publish_requires_at_least_one_decomposed_lesson(mem_db):
    admin = await models.create_user(mem_db, "admin3@example.com", "pw")
    await mem_db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin["id"],))
    await mem_db.commit()

    course = await models.create_course(mem_db, admin["id"], "Course Empty", "desc")
    _ = await models.create_lesson(mem_db, admin["id"], "Not ready", course_id=course["id"])

    with pytest.raises(CoursePublishPreconditionError):
        await publish_course_to_all_users(mem_db, source_course_id=course["id"], actor_user_id=admin["id"])

    # Course stays draft
    updated_course = await models.get_course(mem_db, course["id"])
    assert updated_course["visibility"] == "draft"
