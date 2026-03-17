"""Integration tests for textbook course authoring endpoints."""

from __future__ import annotations

import fitz
import pytest

from backend.db import models


def _build_textbook_pdf_bytes() -> bytes:
    doc = fitz.open()
    try:
        for i in range(9):
            page = doc.new_page()
            page.insert_text((72, 72), f"Textbook page {i + 1}")
        doc.set_toc(
            [
                [1, "Chapter One", 1],
                [1, "Chapter Two", 4],
                [1, "Chapter Three", 7],
            ]
        )
        return doc.tobytes()
    finally:
        doc.close()


@pytest.mark.asyncio
async def test_create_course_requires_admin(client, mem_db):
    user = await models.create_user(mem_db, "nonadmin-create@example.com", "pw")
    resp = await client.post(
        "/courses",
        params={"user_id": user["id"]},
        json={"title": "Unauthorized"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_lesson_decompose_requires_admin(client, mem_db):
    user = await models.create_user(mem_db, "nonadmin-upload@example.com", "pw")
    session_id = await models.create_session(mem_db, user["id"])
    token = await models.create_upload_token(mem_db, session_id)

    resp = await client.post(
        "/lessons/decompose",
        headers={"X-Upload-Token": token},
        data={"session_id": session_id},
        files={"file": ("book.pdf", _build_textbook_pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_textbook_draft_uses_hash_cache_and_exposes_chapters(client, mem_db):
    admin = await models.create_user(mem_db, "admin-textbook@example.com", "pw")
    await mem_db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin["id"],))
    await mem_db.commit()

    pdf_bytes = _build_textbook_pdf_bytes()

    first = await client.post(
        "/courses/textbook/draft",
        params={"user_id": admin["id"]},
        data={"title": "JP Textbook A", "description": "Draft A"},
        files={"file": ("textbook.pdf", pdf_bytes, "application/pdf")},
    )
    assert first.status_code == 201
    first_payload = first.json()
    assert first_payload["source"]["cache_hit"] is False
    assert first_payload["source"]["page_count"] == 9
    assert len(first_payload["chapters"]) >= 3

    second = await client.post(
        "/courses/textbook/draft",
        params={"user_id": admin["id"]},
        data={"title": "JP Textbook B"},
        files={"file": ("same-textbook.pdf", pdf_bytes, "application/pdf")},
    )
    assert second.status_code == 201
    second_payload = second.json()
    assert second_payload["source"]["cache_hit"] is True
    assert second_payload["source"]["pdf_hash"] == first_payload["source"]["pdf_hash"]

    async with mem_db.execute("SELECT COUNT(*) FROM textbook_toc_cache") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert int(row[0]) == 1


@pytest.mark.asyncio
async def test_textbook_chapter_edit_roundtrip(client, mem_db):
    admin = await models.create_user(mem_db, "admin-chapters@example.com", "pw")
    await mem_db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin["id"],))
    await mem_db.commit()

    create = await client.post(
        "/courses/textbook/draft",
        params={"user_id": admin["id"]},
        data={"title": "Editable Book"},
        files={"file": ("editable.pdf", _build_textbook_pdf_bytes(), "application/pdf")},
    )
    assert create.status_code == 201
    payload = create.json()
    course_id = payload["course"]["id"]

    current = await client.get(
        f"/courses/{course_id}/chapters",
        params={"user_id": admin["id"]},
    )
    assert current.status_code == 200
    current_payload = current.json()
    assert len(current_payload["chapters"]) >= 2

    edited = [
        {
            "id": current_payload["chapters"][0]["id"],
            "title": "Intro + Chapter One",
            "page_start": 1,
            "page_end": 5,
            "included": True,
        },
        {
            "id": current_payload["chapters"][1]["id"],
            "title": "Chapter Two Revised",
            "page_start": 6,
            "page_end": 9,
            "included": False,
        },
    ]
    updated = await client.patch(
        f"/courses/{course_id}/chapters",
        params={"user_id": admin["id"]},
        json={"chapters": edited},
    )
    assert updated.status_code == 200
    updated_payload = updated.json()
    assert len(updated_payload["chapters"]) == 2
    assert updated_payload["chapters"][0]["title"] == "Intro + Chapter One"
    assert updated_payload["chapters"][1]["included"] is False
