"""
Authentication endpoints for the BFF.

Flow
----
Register:
  POST /auth/register  →  hash password, call backend to create user,
                          generate token, send verification email.

Verify email:
  POST /auth/verify    →  call backend to consume token & mark verified,
                          create session, return session_id.

Login:
  POST /auth/login     →  look up user, bcrypt check, create session.

Logout:
  POST /auth/logout    →  destroy session in BFF store + backend.

Me (session check):
  GET  /auth/me        →  validate session, return user info.

Resend verification:
  POST /auth/resend    →  generate new token, send email.
"""

from __future__ import annotations

import asyncio
import logging
import secrets

import bcrypt
from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from ..config import settings
from ..email import send_verification_email
from ..http_client import get as get_http
from ..rate_limiter import RateLimiter
from ..session_store import store

log = logging.getLogger(__name__)

# 3 resend emails per email address per hour
_resend_limiter = RateLimiter(capacity=3.0, refill_rate=1 / 1200.0)
# 10 verify attempts per token per minute
_verify_limiter = RateLimiter(capacity=10.0, refill_rate=10 / 60.0)
# 5 login attempts per email per 5 minutes
_login_limiter = RateLimiter(capacity=5.0, refill_rate=5 / 300.0)
# 10 registrations per IP per hour
_register_limiter = RateLimiter(capacity=10.0, refill_rate=10 / 3600.0)

router = APIRouter(prefix="/auth", tags=["auth"])

# ── helpers ────────────────────────────────────────────────────────────────────

def _validate_email(email: str) -> str:
    try:
        info = validate_email(email.strip(), check_deliverability=False)
        return info.normalized
    except EmailNotValidError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _validate_password(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters")


async def _create_backend_session(user_id: str) -> str:
    http = get_http()
    resp = await http.post("/internal/sessions", json={"user_id": user_id})
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Session creation failed")
    return resp.json()["session_id"]


async def _send_verification(user_id: str, email: str) -> None:
    token = secrets.token_urlsafe(32)
    http = get_http()
    await http.post("/internal/auth/verification-tokens", json={"user_id": user_id, "token": token})
    verify_url = f"{settings.APP_URL}/auth/verify?token={token}"
    await send_verification_email(email, verify_url)


# ── request / response models ──────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class VerifyRequest(BaseModel):
    token: str


class ResendRequest(BaseModel):
    email: str


class SessionResponse(BaseModel):
    session_id: str


class MeResponse(BaseModel):
    user_id: str
    email: str
    is_admin: bool = False


# ── routes ─────────────────────────────────────────────────────────────────────

@router.post("/register", status_code=201)
async def register(request: Request, body: RegisterRequest):
    client_ip = request.client.host if request.client else "unknown"
    if not _register_limiter.allow(client_ip):
        raise HTTPException(status_code=429, detail="Too many registration attempts. Please try again later.")
    email = _validate_email(body.email)
    _validate_password(body.password)

    pw_hash = await asyncio.to_thread(
        bcrypt.hashpw, body.password.encode(), bcrypt.gensalt()
    )

    http = get_http()
    resp = await http.post(
        "/internal/auth/register",
        json={"email": email, "password_hash": pw_hash.decode()},
    )

    if resp.status_code == 409:
        detail = resp.json().get("detail", "")
        if detail == "pending_verification":
            raise HTTPException(
                status_code=409,
                detail="pending_verification",
            )
        raise HTTPException(status_code=409, detail="Email already registered. Please log in.")

    if resp.status_code != 201:
        raise HTTPException(status_code=502, detail="Registration failed")

    user = resp.json()
    await _send_verification(user["user_id"], email)
    return {"message": "Verification email sent. Please check your inbox."}


@router.post("/verify", response_model=SessionResponse)
async def verify_email(body: VerifyRequest):
    # Rate-limit by token prefix to prevent brute-force enumeration.
    token_key = body.token[:8] if len(body.token) >= 8 else body.token
    if not _verify_limiter.allow(token_key):
        raise HTTPException(status_code=429, detail="Too many verification attempts. Please try again later.")
    http = get_http()
    resp = await http.post("/internal/auth/verify", json={"token": body.token})
    if resp.status_code == 400:
        raise HTTPException(status_code=400, detail="Invalid or expired verification link")
    if not resp.is_success:
        raise HTTPException(status_code=502, detail="Verification failed")

    user = resp.json()
    session_id = await _create_backend_session(user["user_id"])
    store.add(session_id, user_id=user["user_id"], email=user["email"],
              is_admin=bool(user.get("is_admin", 0)))
    return SessionResponse(session_id=session_id)


@router.post("/login", response_model=SessionResponse)
async def login(body: LoginRequest):
    email = _validate_email(body.email)
    if not _login_limiter.allow(email):
        raise HTTPException(status_code=429, detail="Too many login attempts. Please try again later.")
    http = get_http()
    resp = await http.get("/internal/auth/user", params={"email": email})

    if resp.status_code == 404:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not resp.is_success:
        raise HTTPException(status_code=502, detail="Login failed")

    user = resp.json()

    if not user["email_verified"]:
        raise HTTPException(status_code=403, detail="email_not_verified")

    stored_hash: str = user["password_hash"] or ""
    match = await asyncio.to_thread(
        bcrypt.checkpw, body.password.encode(), stored_hash.encode()
    )
    if not match:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    session_id = await _create_backend_session(user["user_id"])
    store.add(session_id, user_id=user["user_id"], email=email,
              is_admin=bool(user.get("is_admin", 0)))
    return SessionResponse(session_id=session_id)


@router.post("/logout", status_code=204)
async def logout(x_session_id: str = Header(...)):
    entry = store.get(x_session_id)
    if entry:
        store.remove(x_session_id)
        http = get_http()
        await http.delete(f"/internal/sessions/{x_session_id}")


@router.get("/me", response_model=MeResponse)
async def me(x_session_id: str = Header(...)):
    entry = store.get(x_session_id)
    if entry is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return MeResponse(user_id=entry.user_id, email=entry.email, is_admin=entry.is_admin)


@router.post("/resend", status_code=200)
async def resend_verification(body: ResendRequest):
    email = _validate_email(body.email)
    if not _resend_limiter.allow(email):
        raise HTTPException(status_code=429, detail="Too many resend requests. Please try again later.")
    http = get_http()
    resp = await http.get("/internal/auth/user", params={"email": email})
    if resp.status_code == 404:
        # Don't reveal whether email exists
        return {"message": "If that email is registered and unverified, a new link has been sent."}
    user = resp.json()
    if user["email_verified"]:
        return {"message": "Email already verified. Please log in."}
    await _send_verification(user["user_id"], email)
    return {"message": "Verification email resent."}
