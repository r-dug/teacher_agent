"""Integration tests for admin course publish endpoint."""

from __future__ import annotations

import pytest

from backend.db import models


@pytest.mark.asyncio
async def test_publish_requires_admin(client, mem_db):
    user = await models.create_user(mem_db, "owner@example.com", "pw")
    course = await models.create_course(mem_db, user["id"], "Course", "desc")

    resp = await client.post(
        f"/courses/{course['id']}/publish",
        params={"user_id": user["id"]},
        json={},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_publish_returns_409_when_no_decomposed_lessons(client, mem_db):
    admin = await models.create_user(mem_db, "admin-publish@example.com", "pw")
    await mem_db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin["id"],))
    target = await models.create_user(mem_db, "student-publish@example.com", "pw")
    await mem_db.commit()

    course = await models.create_course(mem_db, admin["id"], "JP 101", "desc")
    await models.create_lesson(mem_db, admin["id"], "Undecomposed", course_id=course["id"])

    resp = await client.post(
        f"/courses/{course['id']}/publish",
        params={"user_id": admin["id"]},
        json={},
    )
    assert resp.status_code == 409
    assert "No decomposed lessons are ready yet" in resp.json()["detail"]

    target_courses = await models.list_courses(mem_db, target["id"])
    assert target_courses == []
