"""Tests for session DB helpers."""

import pytest
from backend.db import models, connection as db


@pytest.mark.asyncio
async def test_create_and_get_session(mem_db):
    sid = await models.create_session(mem_db)
    session = await models.get_session(mem_db, sid)
    assert session is not None
    assert session["id"] == sid
    assert session["user_id"] == db.ANON_USER_ID


@pytest.mark.asyncio
async def test_get_nonexistent_session(mem_db):
    result = await models.get_session(mem_db, "does-not-exist")
    assert result is None


@pytest.mark.asyncio
async def test_touch_session(mem_db):
    sid = await models.create_session(mem_db)
    # Just verify it doesn't raise
    await models.touch_session(mem_db, sid)
    session = await models.get_session(mem_db, sid)
    assert session is not None


@pytest.mark.asyncio
async def test_delete_session(mem_db):
    sid = await models.create_session(mem_db)
    await models.delete_session(mem_db, sid)
    assert await models.get_session(mem_db, sid) is None


@pytest.mark.asyncio
async def test_upload_token_lifecycle(mem_db):
    sid = await models.create_session(mem_db)
    token = await models.create_upload_token(mem_db, sid)
    assert token

    # Consuming it returns the session
    session = await models.consume_upload_token(mem_db, token)
    assert session is not None
    assert session["id"] == sid

    # Token is single-use
    again = await models.consume_upload_token(mem_db, token)
    assert again is None


@pytest.mark.asyncio
async def test_expired_upload_token(mem_db):
    """Token with TTL=0 should be immediately invalid."""
    sid = await models.create_session(mem_db)
    token = await models.create_upload_token(mem_db, sid, ttl_seconds=0)
    result = await models.consume_upload_token(mem_db, token)
    assert result is None
