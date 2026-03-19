"""Tests for lesson DB helpers (CRUD + sections + messages)."""

import json
import pytest
from backend.db import models, connection as db


@pytest.mark.asyncio
async def test_create_and_get_lesson(mem_db):
    lid = await models.create_lesson(mem_db, db.ANON_USER_ID, "Test Lesson")
    lesson = await models.get_lesson(mem_db, lid)
    assert lesson is not None
    assert lesson["title"] == "Test Lesson"
    # enrollment state is separate — verify defaults via get_or_create_enrollment
    enrollment = await models.get_or_create_enrollment(mem_db, lid, db.ANON_USER_ID)
    assert enrollment["completed"] == 0
    assert enrollment["current_section_idx"] == 0


@pytest.mark.asyncio
async def test_get_nonexistent_lesson(mem_db):
    assert await models.get_lesson(mem_db, "no-such-id") is None


@pytest.mark.asyncio
async def test_list_lessons(mem_db):
    await models.create_lesson(mem_db, db.ANON_USER_ID, "L1")
    await models.create_lesson(mem_db, db.ANON_USER_ID, "L2")
    rows = await models.list_lessons(mem_db, db.ANON_USER_ID)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_update_enrollment_state(mem_db):
    lid = await models.create_lesson(mem_db, db.ANON_USER_ID, "Lesson")
    enrollment = await models.get_or_create_enrollment(mem_db, lid, db.ANON_USER_ID)
    await models.update_enrollment(mem_db, enrollment["id"], current_section_idx=3, completed=1)
    updated = await models.get_enrollment(mem_db, lid, db.ANON_USER_ID)
    assert updated["current_section_idx"] == 3
    assert updated["completed"] == 1


@pytest.mark.asyncio
async def test_update_lesson_unknown_field_ignored(mem_db):
    """Unknown kwargs should be silently ignored."""
    lid = await models.create_lesson(mem_db, db.ANON_USER_ID, "Lesson")
    await models.update_lesson(mem_db, lid, nonexistent_field="boom")
    assert await models.get_lesson(mem_db, lid) is not None


@pytest.mark.asyncio
async def test_delete_lesson(mem_db):
    lid = await models.create_lesson(mem_db, db.ANON_USER_ID, "ToDelete")
    await models.delete_lesson(mem_db, lid)
    assert await models.get_lesson(mem_db, lid) is None


@pytest.mark.asyncio
async def test_upsert_and_get_sections(mem_db):
    lid = await models.create_lesson(mem_db, db.ANON_USER_ID, "Sectioned")
    sections = [
        {"title": "Intro", "content": "Content A", "key_concepts": ["A", "B"], "page_start": 1, "page_end": 3},
        {"title": "Main",  "content": "Content B", "key_concepts": ["C"],      "page_start": 4, "page_end": 8},
    ]
    await models.upsert_sections(mem_db, lid, sections)
    rows = await models.get_sections(mem_db, lid)
    assert len(rows) == 2
    assert rows[0]["title"] == "Intro"
    assert rows[0]["key_concepts"] == ["A", "B"]
    assert rows[1]["idx"] == 1


@pytest.mark.asyncio
async def test_upsert_sections_replaces(mem_db):
    """Re-upserting should replace old sections, not append."""
    lid = await models.create_lesson(mem_db, db.ANON_USER_ID, "Replace")
    await models.upsert_sections(mem_db, lid, [{"title": "Old", "content": "x", "key_concepts": []}])
    await models.upsert_sections(mem_db, lid, [{"title": "New", "content": "y", "key_concepts": []}])
    rows = await models.get_sections(mem_db, lid)
    assert len(rows) == 1
    assert rows[0]["title"] == "New"


@pytest.mark.asyncio
async def test_upsert_and_get_messages(mem_db):
    lid = await models.create_lesson(mem_db, db.ANON_USER_ID, "WithMessages")
    enrollment = await models.get_or_create_enrollment(mem_db, lid, db.ANON_USER_ID)
    eid = enrollment["id"]
    msgs = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "Hi there"}]},
    ]
    await models.upsert_messages(mem_db, eid, msgs)
    loaded = await models.get_messages(mem_db, eid)
    assert len(loaded) == 2
    assert loaded[0]["role"] == "user"
    assert loaded[0]["content"] == "Hello"
    assert isinstance(loaded[1]["content"], list)


@pytest.mark.asyncio
async def test_messages_replace(mem_db):
    lid = await models.create_lesson(mem_db, db.ANON_USER_ID, "MsgReplace")
    enrollment = await models.get_or_create_enrollment(mem_db, lid, db.ANON_USER_ID)
    eid = enrollment["id"]
    await models.upsert_messages(mem_db, eid, [{"role": "user", "content": "old"}])
    await models.upsert_messages(mem_db, eid, [{"role": "user", "content": "new"}])
    loaded = await models.get_messages(mem_db, eid)
    assert len(loaded) == 1
    assert loaded[0]["content"] == "new"
