"""Integration tests for Phase 2 course authoring endpoints."""

from __future__ import annotations

import hashlib
import json

import fitz
import pytest

from backend.config import settings
from backend.db import connection as db
from backend.db import models


def _build_textbook_pdf_bytes() -> bytes:
    doc = fitz.open()
    try:
        for i in range(8):
            page = doc.new_page()
            page.insert_text((72, 72), f"Chapter page {i + 1}")
        doc.set_toc(
            [
                [1, "Unit 1", 1],
                [1, "Unit 2", 4],
                [1, "Unit 3", 7],
            ]
        )
        return doc.tobytes()
    finally:
        doc.close()


async def _create_admin_textbook_course(mem_db) -> tuple[dict, str]:
    admin = await models.create_user(mem_db, "admin-phase2@example.com", "pw")
    await mem_db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin["id"],))
    course = await models.create_course(mem_db, admin["id"], "Phase 2 Course", "desc")
    course_id = str(course["id"])

    pdf_bytes = _build_textbook_pdf_bytes()
    rel_pdf_path = f"courses/{course_id}.pdf"
    full_pdf_path = settings.STORAGE_DIR / rel_pdf_path
    full_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    full_pdf_path.write_bytes(pdf_bytes)
    pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()

    await mem_db.execute(
        """INSERT INTO course_source_files
           (course_id, creator_id, pdf_hash, pdf_path, page_count, toc_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
        (course_id, admin["id"], pdf_hash, rel_pdf_path, 8, json.dumps([])),
    )
    await mem_db.execute(
        """INSERT INTO course_chapter_drafts
           (id, course_id, idx, title, page_start, page_end, included, created_at, updated_at)
           VALUES (?, ?, 0, 'Unit 1', 1, 4, 1, datetime('now'), datetime('now'))""",
        (db.new_id(), course_id),
    )
    await mem_db.execute(
        """INSERT INTO course_chapter_drafts
           (id, course_id, idx, title, page_start, page_end, included, created_at, updated_at)
           VALUES (?, ?, 1, 'Unit 2', 5, 8, 1, datetime('now'), datetime('now'))""",
        (db.new_id(), course_id),
    )
    await mem_db.commit()
    return admin, course_id


@pytest.mark.asyncio
async def test_advisor_start_message_finalize_roundtrip(client, mem_db, monkeypatch):
    monkeypatch.setattr(
        "backend.services.documents.course_authoring._advisor_reply_sync",
        lambda **_kwargs: "What level of assessment rigor do you want?",
    )
    monkeypatch.setattr(
        "backend.services.documents.course_authoring._objectives_prompt_sync",
        lambda **_kwargs: "Use explicit objectives for each chapter and map quiz checkpoints.",
    )

    admin, course_id = await _create_admin_textbook_course(mem_db)

    start = await client.post(
        f"/courses/{course_id}/advisor/start",
        params={"user_id": admin["id"]},
        json={"reset": False},
    )
    assert start.status_code == 200
    start_payload = start.json()
    assert start_payload["status"] == "draft"
    assert len(start_payload["transcript"]) >= 1

    msg = await client.post(
        f"/courses/{course_id}/advisor/message",
        params={"user_id": admin["id"]},
        json={"text": "Prioritize grammar and chapter quizzes."},
    )
    assert msg.status_code == 200
    msg_payload = msg.json()
    assert msg_payload["status"] == "draft"
    assert msg_payload["transcript"][-1]["role"] == "assistant"

    finalize = await client.post(
        f"/courses/{course_id}/advisor/finalize",
        params={"user_id": admin["id"]},
        json={},
    )
    assert finalize.status_code == 200
    finalize_payload = finalize.json()
    assert finalize_payload["status"] == "finalized"
    assert isinstance(finalize_payload.get("objectives_prompt"), str)
    assert finalize_payload["objectives_prompt"].strip() != ""


@pytest.mark.asyncio
async def test_decompose_start_requires_finalized_advisor(client, mem_db):
    admin, course_id = await _create_admin_textbook_course(mem_db)

    resp = await client.post(
        f"/courses/{course_id}/decompose/start",
        params={"user_id": admin["id"]},
        json={},
    )
    assert resp.status_code == 409
    assert "Finalize advisor objectives" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_decompose_start_launches_job_and_returns_status(client, mem_db, monkeypatch):
    admin, course_id = await _create_admin_textbook_course(mem_db)

    fake_status = {
        "job": {
            "id": "job_123",
            "course_id": course_id,
            "user_id": admin["id"],
            "status": "queued",
            "objectives_prompt": "Prompt",
            "total_items": 2,
            "completed_items": 0,
            "failed_items": 0,
            "progress_pct": 0,
            "notify_session_id": None,
            "error": None,
            "created_at": "2026-01-01T00:00:00Z",
            "started_at": None,
            "finished_at": None,
            "updated_at": "2026-01-01T00:00:00Z",
        },
        "items": [
            {
                "id": "item_1",
                "chapter_id": "chapter_1",
                "idx": 0,
                "title": "Unit 1",
                "page_start": 1,
                "page_end": 3,
                "lesson_id": None,
                "cache_key": None,
                "status": "queued",
                "error": None,
            }
        ],
    }
    launched: dict[str, str] = {}

    async def _fake_create(*_args, **_kwargs):
        return fake_status

    def _fake_launch(job_id: str) -> None:
        launched["job_id"] = job_id

    monkeypatch.setattr("backend.routers.courses.create_decomposition_job", _fake_create)
    monkeypatch.setattr("backend.routers.courses.launch_decomposition_job", _fake_launch)

    resp = await client.post(
        f"/courses/{course_id}/decompose/start",
        params={"user_id": admin["id"]},
        json={},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["job"]["id"] == "job_123"
    assert launched["job_id"] == "job_123"


@pytest.mark.asyncio
async def test_decompose_status_endpoint_returns_service_payload(client, mem_db, monkeypatch):
    admin, course_id = await _create_admin_textbook_course(mem_db)

    expected = {
        "job": {
            "id": "job_999",
            "course_id": course_id,
            "user_id": admin["id"],
            "status": "running",
            "objectives_prompt": "Prompt",
            "total_items": 3,
            "completed_items": 1,
            "failed_items": 0,
            "progress_pct": 33,
            "notify_session_id": None,
            "error": None,
            "created_at": "2026-01-01T00:00:00Z",
            "started_at": "2026-01-01T00:00:01Z",
            "finished_at": None,
            "updated_at": "2026-01-01T00:00:02Z",
        },
        "items": [],
    }

    async def _fake_status(*_args, **_kwargs):
        return expected

    monkeypatch.setattr("backend.routers.courses.get_course_decompose_status", _fake_status)

    resp = await client.get(
        f"/courses/{course_id}/decompose/status",
        params={"user_id": admin["id"], "job_id": "job_999"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["job"]["id"] == "job_999"
    assert payload["job"]["progress_pct"] == 33
