"""Tests for auth-related DB model functions."""

import pytest
from backend.db import models


# ── create_user / get_user_by_email ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_user(mem_db):
    user = await models.create_user(mem_db, "alice@example.com", "hashed_pw")
    assert user["email"] == "alice@example.com"
    assert user["password_hash"] == "hashed_pw"
    assert user["email_verified"] == 0


@pytest.mark.asyncio
async def test_create_user_normalises_email(mem_db):
    user = await models.create_user(mem_db, "  Alice@Example.COM  ", "pw")
    assert user["email"] == "alice@example.com"


@pytest.mark.asyncio
async def test_get_user_by_email_found(mem_db):
    await models.create_user(mem_db, "bob@example.com", "pw")
    user = await models.get_user_by_email(mem_db, "bob@example.com")
    assert user is not None
    assert user["email"] == "bob@example.com"


@pytest.mark.asyncio
async def test_get_user_by_email_case_insensitive(mem_db):
    await models.create_user(mem_db, "carol@example.com", "pw")
    user = await models.get_user_by_email(mem_db, "CAROL@EXAMPLE.COM")
    assert user is not None


@pytest.mark.asyncio
async def test_get_user_by_email_not_found(mem_db):
    result = await models.get_user_by_email(mem_db, "nobody@example.com")
    assert result is None


@pytest.mark.asyncio
async def test_create_user_duplicate_email_raises(mem_db):
    await models.create_user(mem_db, "dup@example.com", "pw")
    with pytest.raises(Exception):
        await models.create_user(mem_db, "dup@example.com", "pw2")


# ── get_user_by_id ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_user_by_id(mem_db):
    user = await models.create_user(mem_db, "dave@example.com", "pw")
    fetched = await models.get_user_by_id(mem_db, user["id"])
    assert fetched is not None
    assert fetched["id"] == user["id"]


@pytest.mark.asyncio
async def test_get_user_by_id_not_found(mem_db):
    result = await models.get_user_by_id(mem_db, "nonexistent-id")
    assert result is None


# ── mark_email_verified ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mark_email_verified(mem_db):
    user = await models.create_user(mem_db, "eve@example.com", "pw")
    assert user["email_verified"] == 0

    await models.mark_email_verified(mem_db, user["id"])
    updated = await models.get_user_by_id(mem_db, user["id"])
    assert updated["email_verified"] == 1


# ── verification tokens ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_consume_verification_token_valid(mem_db):
    user = await models.create_user(mem_db, "frank@example.com", "pw")
    await models.create_verification_token(mem_db, user["id"], "good-token")

    uid = await models.consume_verification_token(mem_db, "good-token")
    assert uid == user["id"]


@pytest.mark.asyncio
async def test_consume_verification_token_single_use(mem_db):
    user = await models.create_user(mem_db, "grace@example.com", "pw")
    await models.create_verification_token(mem_db, user["id"], "one-time")

    first = await models.consume_verification_token(mem_db, "one-time")
    assert first is not None

    second = await models.consume_verification_token(mem_db, "one-time")
    assert second is None


@pytest.mark.asyncio
async def test_consume_verification_token_invalid(mem_db):
    result = await models.consume_verification_token(mem_db, "bad-token")
    assert result is None


@pytest.mark.asyncio
async def test_create_verification_token_replaces_old(mem_db):
    """Creating a new token for the same user deletes the previous one."""
    user = await models.create_user(mem_db, "harry@example.com", "pw")
    await models.create_verification_token(mem_db, user["id"], "old-token")
    await models.create_verification_token(mem_db, user["id"], "new-token")

    # Old token should be gone
    old = await models.consume_verification_token(mem_db, "old-token")
    assert old is None

    # New token should work
    new = await models.consume_verification_token(mem_db, "new-token")
    assert new == user["id"]
