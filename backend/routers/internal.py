"""
Internal endpoints — only called by the frontend server, never by clients.

All routes are prefixed /internal.  In production these should be firewalled
to loopback; in the prototype the backend is already bound to 127.0.0.1.
"""

from __future__ import annotations

from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import connection as db, models
from ..services import terms as terms_config

router = APIRouter(prefix="/internal", tags=["internal"])

Conn = Annotated[asyncpg.Connection, Depends(db.get)]


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
    ttl = min(body.ttl_seconds, 600)
    token = await models.create_upload_token(conn, body.session_id, ttl)
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
    username: str = ""


class AuthUserResponse(BaseModel):
    user_id: str
    email: str
    username: str = ""
    email_verified: bool
    is_admin: bool = False
    password_hash: str | None = None  # only returned on login lookup
    terms_version_accepted: str = ""


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
    user = await models.create_user(conn, body.email, body.password_hash, body.username)
    return AuthUserResponse(
        user_id=user["id"], email=user["email"], username=user.get("username") or "", email_verified=False
    )


@router.get("/auth/user-by-session/{session_id}", response_model=AuthUserResponse)
async def auth_get_user_by_session(session_id: str, conn: Conn):
    """Look up a user by session_id — used by the BFF to restore sessions after restart."""
    session = await models.get_session(conn, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    user = await models.get_user_by_id(conn, session["user_id"])
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return AuthUserResponse(
        user_id=user["id"],
        email=user["email"],
        username=user.get("username") or "",
        email_verified=bool(user["email_verified"]),
        is_admin=bool(user["is_admin"]),
        terms_version_accepted=user.get("terms_version_accepted") or "",
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
        username=user.get("username") or "",
        email_verified=bool(user["email_verified"]),
        is_admin=bool(user["is_admin"]),
        password_hash=user["password_hash"],
        terms_version_accepted=user.get("terms_version_accepted") or "",
    )


@router.post("/auth/verification-tokens", status_code=201)
async def auth_store_token(body: StoreTokenRequest, conn: Conn):
    await models.create_verification_token(conn, body.user_id, body.token)
    return {"ok": True}


class PasswordResetTokenRequest(BaseModel):
    user_id: str
    token: str


class PasswordResetRequest(BaseModel):
    token: str
    password_hash: str


@router.post("/auth/password-reset-tokens", status_code=201)
async def auth_store_reset_token(body: PasswordResetTokenRequest, conn: Conn):
    await models.create_password_reset_token(conn, body.user_id, body.token)
    return {"ok": True}


@router.post("/auth/password-reset", response_model=AuthUserResponse)
async def auth_reset_password(body: PasswordResetRequest, conn: Conn):
    user_id = await models.consume_password_reset_token(conn, body.token)
    if user_id is None:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")
    await models.update_password_hash(conn, user_id, body.password_hash)
    user = await models.get_user_by_id(conn, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return AuthUserResponse(
        user_id=user["id"], email=user["email"],
        username=user.get("username") or "",
        email_verified=bool(user["email_verified"]),
        is_admin=bool(user["is_admin"]),
    )


class DeleteUserRequest(BaseModel):
    user_id: str


@router.post("/auth/delete-user", status_code=204)
async def auth_delete_user(body: DeleteUserRequest, conn: Conn):
    """Permanently delete a user and all their data (cascades via FK constraints)."""
    user = await models.get_user_by_id(conn, body.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    await models.delete_user(conn, body.user_id)


class UpdatePasswordRequest(BaseModel):
    user_id: str
    password_hash: str


@router.post("/auth/update-password", status_code=204)
async def auth_update_password(body: UpdatePasswordRequest, conn: Conn):
    """Update a user's password hash directly (current-password check happens in BFF)."""
    user = await models.get_user_by_id(conn, body.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    await models.update_password_hash(conn, body.user_id, body.password_hash)


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
        user_id=user["id"], email=user["email"],
        username=user.get("username") or "",
        email_verified=True,
        is_admin=bool(user["is_admin"]),
    )


class UpdateUsernameRequest(BaseModel):
    user_id: str
    username: str


@router.patch("/auth/username")
async def auth_update_username(body: UpdateUsernameRequest, conn: Conn):
    import re as _re
    uname = body.username.strip()
    if not uname or not _re.match(r'^[a-zA-Z][a-zA-Z0-9_]{2,29}$', uname):
        raise HTTPException(
            status_code=400,
            detail="Username must be 3-30 characters, start with a letter, and contain only letters, numbers, or underscores.",
        )
    try:
        await models.update_username(conn, body.user_id, uname)
    except Exception as exc:
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(status_code=409, detail="Username is already taken.")
        raise
    return {"ok": True, "username": uname}


# ── terms of service ──────────────────────────────────────────────────────────

class TermsResponse(BaseModel):
    version: str
    text: str
    license_name: str
    license_url: str
    copyright_holder: str
    copyright_year: str


class TermsAcceptRequest(BaseModel):
    user_id: str


@router.get("/terms", response_model=TermsResponse)
async def get_terms():
    """Return the current Terms of Use text + version + license metadata."""
    return TermsResponse(
        version=terms_config.TOS_VERSION,
        text=terms_config.TOS_TEXT,
        license_name=terms_config.LICENSE_NAME,
        license_url=terms_config.LICENSE_URL,
        copyright_holder=terms_config.COPYRIGHT_HOLDER,
        copyright_year=terms_config.COPYRIGHT_YEAR,
    )


@router.post("/terms/accept", status_code=204)
async def accept_terms(body: TermsAcceptRequest, conn: Conn):
    """Record that ``user_id`` has accepted the current TOS_VERSION."""
    user = await models.get_user_by_id(conn, body.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    await models.set_terms_accepted(conn, body.user_id, terms_config.TOS_VERSION)
