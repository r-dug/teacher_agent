"""
Tests for the BFF auth router (frontend/routers/auth.py).

Backend HTTP calls are intercepted with a fake httpx transport so no real
backend server is needed.  Email sending is patched to a no-op coroutine.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import bcrypt
import httpx
import pytest
import pytest_asyncio

from frontend.session_store import SessionStore


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


# ── fake backend transport ─────────────────────────────────────────────────────

class FakeBackend:
    """
    In-memory backend that handles the subset of internal endpoints
    called by the BFF auth router.
    """

    def __init__(self):
        self._users: dict[str, dict] = {}   # email → user row
        self._tokens: dict[str, str] = {}   # token → user_id
        self._sessions: dict[str, str] = {} # session_id → user_id
        self._id_counter = 0

    def _new_id(self) -> str:
        self._id_counter += 1
        return f"user-{self._id_counter}"

    def _sess_id(self) -> str:
        self._id_counter += 1
        return f"sess-{self._id_counter}"

    def handle(self, request: httpx.Request) -> httpx.Response:
        method = request.method
        path = request.url.path
        params = dict(request.url.params)
        body = {}
        if request.content:
            try:
                body = json.loads(request.content)
            except Exception:
                pass

        # POST /internal/auth/register
        if method == "POST" and path == "/internal/auth/register":
            email = body["email"].lower().strip()
            if email in self._users:
                status = (
                    "pending_verification"
                    if not self._users[email]["email_verified"]
                    else "already_registered"
                )
                return httpx.Response(409, json={"detail": status})
            uid = self._new_id()
            self._users[email] = {
                "id": uid,
                "email": email,
                "password_hash": body["password_hash"],
                "email_verified": False,
            }
            return httpx.Response(201, json={"user_id": uid, "email": email, "email_verified": False})

        # GET /internal/auth/user
        if method == "GET" and path == "/internal/auth/user":
            email = params.get("email", "").lower().strip()
            user = self._users.get(email)
            if user is None:
                return httpx.Response(404, json={"detail": "not found"})
            return httpx.Response(200, json={
                "user_id": user["id"],
                "email": user["email"],
                "email_verified": user["email_verified"],
                "password_hash": user["password_hash"],
            })

        # POST /internal/auth/verification-tokens
        if method == "POST" and path == "/internal/auth/verification-tokens":
            self._tokens[body["token"]] = body["user_id"]
            return httpx.Response(201, json={"ok": True})

        # POST /internal/auth/verify
        if method == "POST" and path == "/internal/auth/verify":
            token = body.get("token", "")
            uid = self._tokens.pop(token, None)
            if uid is None:
                return httpx.Response(400, json={"detail": "invalid token"})
            # Mark verified
            for user in self._users.values():
                if user["id"] == uid:
                    user["email_verified"] = True
                    email = user["email"]
                    break
            else:
                return httpx.Response(404, json={"detail": "user not found"})
            return httpx.Response(200, json={"user_id": uid, "email": email, "email_verified": True})

        # POST /internal/sessions
        if method == "POST" and path == "/internal/sessions":
            sid = self._sess_id()
            uid = body.get("user_id", "anon")
            self._sessions[sid] = uid
            return httpx.Response(200, json={"session_id": sid, "user_id": uid})

        # DELETE /internal/sessions/{sid}
        if method == "DELETE" and path.startswith("/internal/sessions/"):
            sid = path.split("/")[-1]
            self._sessions.pop(sid, None)
            return httpx.Response(204)

        return httpx.Response(404, json={"detail": f"Unhandled: {method} {path}"})


class _FakeTransport(httpx.AsyncBaseTransport):
    def __init__(self, backend: FakeBackend):
        self._backend = backend

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._backend.handle(request)


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def bff_client():
    """
    AsyncClient for the frontend FastAPI app, with:
      - backend HTTP calls routed to FakeBackend
      - email sending stubbed out
      - a fresh SessionStore per test
    """
    from httpx import AsyncClient, ASGITransport
    from frontend.main import app
    from frontend import http_client

    backend = FakeBackend()
    fake_http = httpx.AsyncClient(
        transport=_FakeTransport(backend),
        base_url="http://backend",
    )

    # Fresh session store so tests are isolated
    fresh_store = SessionStore()

    with patch.object(http_client, "_client", fake_http):
        with patch("frontend.routers.auth.store", fresh_store):
            with patch(
                "frontend.routers.auth.send_verification_email",
                new=AsyncMock(return_value=None),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="http://test",
                ) as ac:
                    # Expose backend for direct manipulation in tests
                    ac._fake_backend = backend  # type: ignore[attr-defined]
                    ac._store = fresh_store     # type: ignore[attr-defined]
                    yield ac


# ── registration ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_success(bff_client):
    resp = await bff_client.post(
        "/auth/register",
        json={"email": "alice@example.com", "password": "password123"},
    )
    assert resp.status_code == 201
    assert "message" in resp.json()


@pytest.mark.asyncio
async def test_register_invalid_email(bff_client):
    resp = await bff_client.post(
        "/auth/register",
        json={"email": "not-an-email", "password": "password123"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_password_too_short(bff_client):
    resp = await bff_client.post(
        "/auth/register",
        json={"email": "short@example.com", "password": "abc"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_duplicate_pending(bff_client):
    await bff_client.post(
        "/auth/register",
        json={"email": "dup@example.com", "password": "password123"},
    )
    resp = await bff_client.post(
        "/auth/register",
        json={"email": "dup@example.com", "password": "password123"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "pending_verification"


# ── email verification ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verify_valid_token(bff_client):
    backend: FakeBackend = bff_client._fake_backend

    # Register to get a user in the backend store
    await bff_client.post(
        "/auth/register",
        json={"email": "verify@example.com", "password": "password123"},
    )

    # The register call triggers _send_verification which posts a token.
    # Grab it from the fake backend's token store.
    assert len(backend._tokens) == 1
    token = next(iter(backend._tokens))

    resp = await bff_client.post("/auth/verify", json={"token": token})
    assert resp.status_code == 200
    assert "session_id" in resp.json()


@pytest.mark.asyncio
async def test_verify_invalid_token(bff_client):
    resp = await bff_client.post("/auth/verify", json={"token": "bogus"})
    assert resp.status_code == 400


# ── login ──────────────────────────────────────────────────────────────────────

async def _register_and_verify(bff_client, email: str, password: str) -> str:
    """Helper: register + verify + return session_id."""
    backend: FakeBackend = bff_client._fake_backend

    await bff_client.post("/auth/register", json={"email": email, "password": password})
    token = next(iter(backend._tokens))

    resp = await bff_client.post("/auth/verify", json={"token": token})
    return resp.json()["session_id"]


@pytest.mark.asyncio
async def test_login_success(bff_client):
    await _register_and_verify(bff_client, "login@example.com", "password123")

    resp = await bff_client.post(
        "/auth/login",
        json={"email": "login@example.com", "password": "password123"},
    )
    assert resp.status_code == 200
    assert "session_id" in resp.json()


@pytest.mark.asyncio
async def test_login_wrong_password(bff_client):
    await _register_and_verify(bff_client, "wp@example.com", "correct-password")

    resp = await bff_client.post(
        "/auth/login",
        json={"email": "wp@example.com", "password": "wrong-password"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_unverified(bff_client):
    await bff_client.post(
        "/auth/register",
        json={"email": "unverified@example.com", "password": "password123"},
    )
    resp = await bff_client.post(
        "/auth/login",
        json={"email": "unverified@example.com", "password": "password123"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "email_not_verified"


@pytest.mark.asyncio
async def test_login_unknown_email(bff_client):
    resp = await bff_client.post(
        "/auth/login",
        json={"email": "ghost@example.com", "password": "password123"},
    )
    assert resp.status_code == 401


# ── me / session check ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_me_valid_session(bff_client):
    sid = await _register_and_verify(bff_client, "me@example.com", "password123")

    resp = await bff_client.get("/auth/me", headers={"X-Session-Id": sid})
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "me@example.com"
    assert "user_id" in data


@pytest.mark.asyncio
async def test_me_invalid_session(bff_client):
    resp = await bff_client.get("/auth/me", headers={"X-Session-Id": "fake-session"})
    assert resp.status_code == 401


# ── logout ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_logout(bff_client):
    store: SessionStore = bff_client._store
    sid = await _register_and_verify(bff_client, "logout@example.com", "password123")

    assert store.get(sid) is not None

    resp = await bff_client.post("/auth/logout", headers={"X-Session-Id": sid})
    assert resp.status_code == 204

    assert store.get(sid) is None


@pytest.mark.asyncio
async def test_me_after_logout(bff_client):
    sid = await _register_and_verify(bff_client, "after@example.com", "password123")

    await bff_client.post("/auth/logout", headers={"X-Session-Id": sid})
    resp = await bff_client.get("/auth/me", headers={"X-Session-Id": sid})
    assert resp.status_code == 401


# ── resend verification ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resend_for_unverified(bff_client):
    backend: FakeBackend = bff_client._fake_backend
    await bff_client.post(
        "/auth/register",
        json={"email": "resend@example.com", "password": "password123"},
    )
    backend._tokens.clear()  # clear first token

    resp = await bff_client.post("/auth/resend", json={"email": "resend@example.com"})
    assert resp.status_code == 200
    assert len(backend._tokens) == 1  # new token issued


@pytest.mark.asyncio
async def test_resend_for_verified(bff_client):
    backend: FakeBackend = bff_client._fake_backend
    await _register_and_verify(bff_client, "done@example.com", "password123")
    backend._tokens.clear()

    resp = await bff_client.post("/auth/resend", json={"email": "done@example.com"})
    assert resp.status_code == 200
    # No new token because email is already verified
    assert len(backend._tokens) == 0


@pytest.mark.asyncio
async def test_resend_for_unknown_email(bff_client):
    # Should return 200 and not reveal whether the email exists
    resp = await bff_client.post("/auth/resend", json={"email": "nobody@example.com"})
    assert resp.status_code == 200
