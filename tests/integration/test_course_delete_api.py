"""Integration tests for course deletion behavior."""

from __future__ import annotations

import pytest

from backend.db import models


@pytest.mark.asyncio
async def test_delete_course_without_cascade_keeps_lessons_as_standalone(client, mem_db):
    user = await models.create_user(mem_db, "delete-course-user@example.com", "pw")
    course = await models.create_course(mem_db, user["id"], "Course A", "desc")
    lesson_id = await models.create_lesson(
        mem_db,
        user["id"],
        "Lesson A",
        course_id=course["id"],
    )

    resp = await client.delete(
        f"/courses/{course['id']}",
        params={"user_id": user["id"]},
    )
    assert resp.status_code == 204

    lesson = await models.get_lesson(mem_db, lesson_id)
    assert lesson is not None
    assert lesson["course_id"] is None


@pytest.mark.asyncio
async def test_delete_course_with_cascade_deletes_lessons(client, mem_db):
    user = await models.create_user(mem_db, "delete-course-cascade@example.com", "pw")
    course = await models.create_course(mem_db, user["id"], "Course B", "desc")
    lesson_id = await models.create_lesson(
        mem_db,
        user["id"],
        "Lesson B",
        course_id=course["id"],
    )

    resp = await client.delete(
        f"/courses/{course['id']}",
        params={"user_id": user["id"], "cascade_lessons": "true"},
    )
    assert resp.status_code == 204

    lesson = await models.get_lesson(mem_db, lesson_id)
    assert lesson is None
