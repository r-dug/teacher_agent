"""Tests for admin course publication service."""

from __future__ import annotations

import pytest

from backend.db import models
from backend.services.course_publish import (
    CoursePublishPreconditionError,
    publish_course_to_all_users,
)


@pytest.mark.asyncio
async def test_publish_only_decomposed_lessons(mem_db):
    # admin source user
    admin = await models.create_user(mem_db, "admin@example.com", "pw")
    await mem_db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin["id"],))
    # target non-admin user
    user = await models.create_user(mem_db, "student@example.com", "pw")
    await mem_db.commit()

    course = await models.create_course(mem_db, admin["id"], "Starter JP", "desc")
    decomposed_id = await models.create_lesson(mem_db, admin["id"], "L1", course_id=course["id"])
    undecomposed_id = await models.create_lesson(mem_db, admin["id"], "L2", course_id=course["id"])
    await models.upsert_sections(
        mem_db,
        decomposed_id,
        [{"title": "s", "content": "c", "key_concepts": ["k"], "page_start": 1, "page_end": 1}],
    )
    await models.upsert_messages(mem_db, decomposed_id, [{"role": "assistant", "content": "old"}])

    result = await publish_course_to_all_users(
        mem_db,
        source_course_id=course["id"],
        actor_user_id=admin["id"],
    )

    assert result["target_users"] == 1
    assert result["published_courses"] == 1
    assert result["published_lessons"] == 1
    assert result["skipped_lessons"] == 1

    target_courses = await models.list_courses(mem_db, user["id"])
    assert len(target_courses) == 1
    cloned_lessons = await models.list_lessons(
        mem_db, user["id"], course_id=target_courses[0]["id"], limit=20, offset=0
    )
    assert len(cloned_lessons) == 1
    cloned = cloned_lessons[0]
    assert cloned["title"] == "L1"
    assert cloned["current_section_idx"] == 0
    assert cloned["completed"] == 0
    assert await models.get_messages(mem_db, cloned["id"]) == []
    sections = await models.get_sections(mem_db, cloned["id"])
    assert len(sections) == 1
    assert sections[0]["content"] == "c"
    assert undecomposed_id != cloned["id"]


@pytest.mark.asyncio
async def test_publish_is_idempotent_and_updates_existing_clone(mem_db):
    admin = await models.create_user(mem_db, "admin2@example.com", "pw")
    await mem_db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin["id"],))
    user = await models.create_user(mem_db, "student2@example.com", "pw")
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

    # change source content and republish; clone should update, not duplicate
    await models.update_course(mem_db, course["id"], title="Course A v2")
    await models.update_lesson(mem_db, lesson_id, title="Lesson A v2")
    await models.upsert_sections(
        mem_db,
        lesson_id,
        [{"title": "S1", "content": "C2", "key_concepts": ["x"]}],
    )
    second = await publish_course_to_all_users(mem_db, source_course_id=course["id"], actor_user_id=admin["id"])
    assert second["published_lessons"] == 1

    target_courses = await models.list_courses(mem_db, user["id"])
    assert len(target_courses) == 1
    assert target_courses[0]["title"] == "Course A v2"

    cloned_lessons = await models.list_lessons(
        mem_db, user["id"], course_id=target_courses[0]["id"], limit=20, offset=0
    )
    assert len(cloned_lessons) == 1
    assert cloned_lessons[0]["title"] == "Lesson A v2"
    sections = await models.get_sections(mem_db, cloned_lessons[0]["id"])
    assert len(sections) == 1
    assert sections[0]["content"] == "C2"


@pytest.mark.asyncio
async def test_publish_requires_at_least_one_decomposed_lesson(mem_db):
    admin = await models.create_user(mem_db, "admin3@example.com", "pw")
    await mem_db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin["id"],))
    user = await models.create_user(mem_db, "student3@example.com", "pw")
    await mem_db.commit()

    course = await models.create_course(mem_db, admin["id"], "Course Empty", "desc")
    _ = await models.create_lesson(mem_db, admin["id"], "Not ready", course_id=course["id"])

    with pytest.raises(CoursePublishPreconditionError):
        await publish_course_to_all_users(mem_db, source_course_id=course["id"], actor_user_id=admin["id"])

    target_courses = await models.list_courses(mem_db, user["id"])
    assert target_courses == []
