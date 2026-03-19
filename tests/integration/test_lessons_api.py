"""
Integration tests for the lessons REST API.

Uses the shared ``client`` (httpx AsyncClient via ASGITransport) and ``mem_db``
fixtures from tests/conftest.py.  The lifespan is NOT invoked, so app_state
models are None — fine because these routes only touch the DB.
"""

from __future__ import annotations

import pytest

from backend.db import connection as db, models


# ── list lessons ──────────────────────────────────────────────────────────────

async def test_list_lessons_empty(client, mem_db):
    resp = await client.get("/lessons", params={"user_id": db.ANON_USER_ID})
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_lessons_returns_created_lesson(client, mem_db):
    lesson_id = await models.create_lesson(mem_db, db.ANON_USER_ID, "My Lesson")

    resp = await client.get("/lessons", params={"user_id": db.ANON_USER_ID})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == lesson_id
    assert data[0]["title"] == "My Lesson"
    assert data[0]["completed"] is False


async def test_list_lessons_multiple(client, mem_db):
    for title in ("Alpha", "Beta", "Gamma"):
        await models.create_lesson(mem_db, db.ANON_USER_ID, title)

    resp = await client.get("/lessons", params={"user_id": db.ANON_USER_ID})
    assert resp.status_code == 200
    assert len(resp.json()) == 3


# ── get lesson ────────────────────────────────────────────────────────────────

async def test_get_lesson_basic(client, mem_db):
    lesson_id = await models.create_lesson(mem_db, db.ANON_USER_ID, "Test Lesson")

    resp = await client.get(f"/lessons/{lesson_id}", params={"user_id": db.ANON_USER_ID})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == lesson_id
    assert data["title"] == "Test Lesson"
    assert data["sections"] == []
    assert data["messages"] == []


async def test_get_lesson_with_sections_and_messages(client, mem_db):
    lesson_id = await models.create_lesson(mem_db, db.ANON_USER_ID, "Rich Lesson")
    await models.upsert_sections(mem_db, lesson_id, [
        {
            "title": "Chapter 1",
            "content": "First section content.",
            "key_concepts": ["concept A", "concept B"],
            "page_start": 1,
            "page_end": 5,
        },
        {
            "title": "Chapter 2",
            "content": "Second section content.",
            "key_concepts": [],
            "page_start": 6,
            "page_end": 10,
        },
    ])
    enrollment = await models.get_or_create_enrollment(mem_db, lesson_id, db.ANON_USER_ID)
    await models.upsert_messages(mem_db, enrollment["id"], [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ])

    resp = await client.get(f"/lessons/{lesson_id}", params={"user_id": db.ANON_USER_ID})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["sections"]) == 2
    assert data["sections"][0]["title"] == "Chapter 1"
    assert data["sections"][1]["title"] == "Chapter 2"
    assert len(data["messages"]) == 2
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][1]["role"] == "assistant"


async def test_get_lesson_not_found(client, mem_db):
    resp = await client.get("/lessons/does-not-exist", params={"user_id": db.ANON_USER_ID})
    assert resp.status_code == 404


# ── patch lesson ──────────────────────────────────────────────────────────────

async def test_patch_lesson_title(client, mem_db):
    lesson_id = await models.create_lesson(mem_db, db.ANON_USER_ID, "Old Title")

    resp = await client.patch(
        f"/lessons/{lesson_id}",
        params={"user_id": db.ANON_USER_ID},
        json={"title": "New Title"},
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "New Title"


async def test_patch_lesson_visibility(client, mem_db):
    lesson_id = await models.create_lesson(mem_db, db.ANON_USER_ID, "Draft Lesson")

    resp = await client.patch(
        f"/lessons/{lesson_id}",
        params={"user_id": db.ANON_USER_ID},
        json={"visibility": "published"},
    )
    assert resp.status_code == 200
    assert resp.json()["visibility"] == "published"


async def test_patch_lesson_not_found(client, mem_db):
    resp = await client.patch(
        "/lessons/nonexistent",
        params={"user_id": db.ANON_USER_ID},
        json={"title": "x"},
    )
    assert resp.status_code == 404


# ── delete lesson ─────────────────────────────────────────────────────────────

async def test_delete_lesson(client, mem_db):
    lesson_id = await models.create_lesson(mem_db, db.ANON_USER_ID, "To Delete")

    resp = await client.delete(f"/lessons/{lesson_id}", params={"user_id": db.ANON_USER_ID})
    assert resp.status_code == 204

    # Confirm it's gone
    assert await models.get_lesson(mem_db, lesson_id) is None


async def test_delete_lesson_not_found(client, mem_db):
    resp = await client.delete("/lessons/nonexistent", params={"user_id": db.ANON_USER_ID})
    assert resp.status_code == 404


async def test_delete_lesson_not_in_list_afterwards(client, mem_db):
    lesson_id = await models.create_lesson(mem_db, db.ANON_USER_ID, "Ephemeral")
    await client.delete(f"/lessons/{lesson_id}", params={"user_id": db.ANON_USER_ID})

    resp = await client.get("/lessons", params={"user_id": db.ANON_USER_ID})
    ids = [item["id"] for item in resp.json()]
    assert lesson_id not in ids


# ── save lesson state ─────────────────────────────────────────────────────────

async def test_save_lesson_state_updates_progress(client, mem_db):
    lesson_id = await models.create_lesson(mem_db, db.ANON_USER_ID, "Save Test")

    resp = await client.post(
        f"/lessons/save/{lesson_id}",
        params={"user_id": db.ANON_USER_ID},
        json={"current_section_idx": 2, "completed": False},
    )
    assert resp.status_code == 204

    enrollment = await models.get_enrollment(mem_db, lesson_id, db.ANON_USER_ID)
    assert enrollment["current_section_idx"] == 2
    assert not enrollment["completed"]


async def test_save_lesson_state_persists_messages(client, mem_db):
    lesson_id = await models.create_lesson(mem_db, db.ANON_USER_ID, "Save Test")

    body = {
        "current_section_idx": 1,
        "completed": False,
        "messages": [
            {"role": "user", "content": "What is this?"},
            {"role": "assistant", "content": "Great question!"},
        ],
    }
    resp = await client.post(
        f"/lessons/save/{lesson_id}",
        params={"user_id": db.ANON_USER_ID},
        json=body,
    )
    assert resp.status_code == 204

    enrollment = await models.get_enrollment(mem_db, lesson_id, db.ANON_USER_ID)
    messages = await models.get_messages(mem_db, enrollment["id"])
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"


async def test_save_lesson_state_persists_sections(client, mem_db):
    lesson_id = await models.create_lesson(mem_db, db.ANON_USER_ID, "Save Test")

    body = {
        "current_section_idx": 0,
        "completed": False,
        "sections": [
            {
                "title": "Intro",
                "content": "Overview.",
                "key_concepts": ["A"],
                "page_start": 1,
                "page_end": 3,
            }
        ],
    }
    resp = await client.post(
        f"/lessons/save/{lesson_id}",
        params={"user_id": db.ANON_USER_ID},
        json=body,
    )
    assert resp.status_code == 204

    sections = await models.get_sections(mem_db, lesson_id)
    assert len(sections) == 1
    assert sections[0]["title"] == "Intro"


async def test_save_lesson_state_not_found(client, mem_db):
    resp = await client.post(
        "/lessons/save/nonexistent",
        params={"user_id": db.ANON_USER_ID},
        json={"current_section_idx": 0},
    )
    assert resp.status_code == 404
