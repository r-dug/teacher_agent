"""Tests for auth-related backend internal API endpoints."""

import pytest
from backend.db import models


# ── POST /internal/auth/register ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_new_user(client, mem_db):
    resp = await client.post(
        "/internal/auth/register",
        json={"email": "new@example.com", "password_hash": "hashed"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "new@example.com"
    assert data["email_verified"] is False
    assert "user_id" in data


@pytest.mark.asyncio
async def test_register_duplicate_unverified(client, mem_db):
    await client.post(
        "/internal/auth/register",
        json={"email": "dup@example.com", "password_hash": "pw"},
    )
    resp = await client.post(
        "/internal/auth/register",
        json={"email": "dup@example.com", "password_hash": "pw2"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "pending_verification"


@pytest.mark.asyncio
async def test_register_duplicate_verified(client, mem_db):
    r1 = await client.post(
        "/internal/auth/register",
        json={"email": "verified@example.com", "password_hash": "pw"},
    )
    user_id = r1.json()["user_id"]
    await models.mark_email_verified(mem_db, user_id)

    resp = await client.post(
        "/internal/auth/register",
        json={"email": "verified@example.com", "password_hash": "pw2"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "already_registered"


# ── GET /internal/auth/user ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_user_found(client, mem_db):
    await client.post(
        "/internal/auth/register",
        json={"email": "lookup@example.com", "password_hash": "secret"},
    )
    resp = await client.get("/internal/auth/user", params={"email": "lookup@example.com"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "lookup@example.com"
    assert data["password_hash"] == "secret"


@pytest.mark.asyncio
async def test_get_user_not_found(client, mem_db):
    resp = await client.get("/internal/auth/user", params={"email": "ghost@example.com"})
    assert resp.status_code == 404


# ── POST /internal/auth/verification-tokens ────────────────────────────────────

@pytest.mark.asyncio
async def test_store_and_verify_token(client, mem_db):
    r1 = await client.post(
        "/internal/auth/register",
        json={"email": "verify@example.com", "password_hash": "pw"},
    )
    user_id = r1.json()["user_id"]

    store_resp = await client.post(
        "/internal/auth/verification-tokens",
        json={"user_id": user_id, "token": "tok-123"},
    )
    assert store_resp.status_code == 201

    verify_resp = await client.post(
        "/internal/auth/verify",
        json={"token": "tok-123"},
    )
    assert verify_resp.status_code == 200
    data = verify_resp.json()
    assert data["email_verified"] is True
    assert data["user_id"] == user_id


@pytest.mark.asyncio
async def test_verify_invalid_token(client, mem_db):
    resp = await client.post(
        "/internal/auth/verify",
        json={"token": "bad-token"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_verify_token_single_use(client, mem_db):
    r1 = await client.post(
        "/internal/auth/register",
        json={"email": "once@example.com", "password_hash": "pw"},
    )
    user_id = r1.json()["user_id"]
    await client.post(
        "/internal/auth/verification-tokens",
        json={"user_id": user_id, "token": "once-tok"},
    )

    first = await client.post("/internal/auth/verify", json={"token": "once-tok"})
    assert first.status_code == 200

    second = await client.post("/internal/auth/verify", json={"token": "once-tok"})
    assert second.status_code == 400
