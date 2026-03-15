"""
Internal endpoints — only called by the frontend server, never by clients.

All routes are prefixed /internal.  In production these should be firewalled
to loopback; in the prototype the backend is already bound to 127.0.0.1.
"""

from __future__ import annotations

from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import connection as db, models

router = APIRouter(prefix="/internal", tags=["internal"])

Conn = Annotated[aiosqlite.Connection, Depends(db.get)]


class UploadTokenRequest(BaseModel):
    session_id: str
    ttl_seconds: int = 300


class UploadTokenResponse(BaseModel):
    token: str


@router.post("/upload_tokens", response_model=UploadTokenResponse)
async def create_upload_token(body: UploadTokenRequest, conn: Conn):
    """
    Issue a short-lived upload token for the given session.
    Called by the frontend server when the client requests an upload URL.
    """
    session = await models.get_session(conn, body.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    token = await models.create_upload_token(conn, body.session_id, body.ttl_seconds)
    return UploadTokenResponse(token=token)


class SessionCreateRequest(BaseModel):
    user_id: str | None = None


class SessionCreateResponse(BaseModel):
    session_id: str
    user_id: str


@router.post("/sessions", response_model=SessionCreateResponse)
async def create_session(body: SessionCreateRequest, conn: Conn):
    """Create a backend session record and return its id."""
    user_id = body.user_id or db.ANON_USER_ID
    session_id = await models.create_session(conn, user_id)
    return SessionCreateResponse(session_id=session_id, user_id=user_id)


@router.get("/sessions/{session_id}", response_model=SessionCreateResponse)
async def get_session(session_id: str, conn: Conn):
    """Return the user_id for an existing session."""
    session = await models.get_session(conn, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionCreateResponse(session_id=session_id, user_id=session["user_id"])


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, conn: Conn):
    await models.delete_session(conn, session_id)


# ── auth ───────────────────────────────────────────────────────────────────────

class AuthRegisterRequest(BaseModel):
    email: str
    password_hash: str


class AuthUserResponse(BaseModel):
    user_id: str
    email: str
    email_verified: bool
    is_admin: bool = False
    password_hash: str | None = None  # only returned on login lookup


class StoreTokenRequest(BaseModel):
    user_id: str
    token: str


class VerifyTokenRequest(BaseModel):
    token: str


@router.post("/auth/register", response_model=AuthUserResponse, status_code=201)
async def auth_register(body: AuthRegisterRequest, conn: Conn):
    existing = await models.get_user_by_email(conn, body.email)
    if existing:
        status = (
            "pending_verification"
            if not existing["email_verified"]
            else "already_registered"
        )
        raise HTTPException(status_code=409, detail=status)
    user = await models.create_user(conn, body.email, body.password_hash)
    return AuthUserResponse(
        user_id=user["id"], email=user["email"], email_verified=False
    )


@router.get("/auth/user", response_model=AuthUserResponse)
async def auth_get_user(email: str, conn: Conn):
    """Look up a user by email for login; returns password_hash for BFF to verify."""
    user = await models.get_user_by_email(conn, email)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return AuthUserResponse(
        user_id=user["id"],
        email=user["email"],
        email_verified=bool(user["email_verified"]),
        is_admin=bool(user["is_admin"]),
        password_hash=user["password_hash"],
    )


@router.post("/auth/verification-tokens", status_code=201)
async def auth_store_token(body: StoreTokenRequest, conn: Conn):
    await models.create_verification_token(conn, body.user_id, body.token)
    return {"ok": True}


@router.post("/auth/verify", response_model=AuthUserResponse)
async def auth_verify_email(body: VerifyTokenRequest, conn: Conn):
    user_id = await models.consume_verification_token(conn, body.token)
    if user_id is None:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    await models.mark_email_verified(conn, user_id)
    user = await models.get_user_by_id(conn, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return AuthUserResponse(
        user_id=user["id"], email=user["email"], email_verified=True,
        is_admin=bool(user["is_admin"]),
    )
