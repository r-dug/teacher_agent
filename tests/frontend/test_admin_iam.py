"""Tests for BFF admin-route freshness checks."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio

from frontend.session_store import SessionStore


class FakeAdminBackend:
    def __init__(self) -> None:
        self.is_admin = True
        self.authoritative_user_id = "admin-user"
        self.last_actor_user_id: str | None = None
        self.last_courses_user_id: str | None = None
        self.last_iam_actor_user_id: str | None = None
        self.last_iam_target_user_id: str | None = None
        self.iam_patch_status = 200
        self.iam_patch_payload: dict = {
            "user": {
                "id": "target-1",
                "email": "target@example.com",
                "display_name": None,
                "email_verified": True,
                "is_admin": True,
                "bootstrap_managed": False,
                "created_at": "2026-03-20 10:00:00",
            },
            "warning": None,
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        method = request.method
        path = request.url.path
        params = dict(request.url.params)

        if method == "GET" and path.startswith("/internal/auth/user-by-session/"):
            return httpx.Response(
                200,
                json={
                    "user_id": self.authoritative_user_id,
                    "email": "admin@example.com",
                    "email_verified": True,
                    "is_admin": self.is_admin,
                },
            )

        if method == "GET" and path == "/admin/usage/live":
            self.last_actor_user_id = params.get("actor_user_id")
            return httpx.Response(200, json={"actor_user_id": self.last_actor_user_id})

        if method == "POST" and path == "/courses":
            self.last_courses_user_id = params.get("user_id")
            return httpx.Response(201, json={"id": "course-1", "forwarded_user_id": self.last_courses_user_id})

        if method == "GET" and path == "/admin/iam/users":
            self.last_iam_actor_user_id = params.get("actor_user_id")
            return httpx.Response(
                200,
                json={
                    "users": [
                        {
                            "id": "u-1",
                            "email": "u1@example.com",
                            "display_name": None,
                            "email_verified": True,
                            "is_admin": True,
                            "bootstrap_managed": False,
                            "created_at": "2026-03-20 10:00:00",
                        }
                    ],
                },
            )

        if method == "PATCH" and path.startswith("/admin/iam/users/") and path.endswith("/admin"):
            target_user_id = path.split("/")[-2]
            self.last_iam_target_user_id = target_user_id
            self.last_iam_actor_user_id = params.get("actor_user_id")
            return httpx.Response(self.iam_patch_status, json=self.iam_patch_payload)

        return httpx.Response(404, json={"detail": f"Unhandled: {method} {path}"})


class _FakeTransport(httpx.AsyncBaseTransport):
    def __init__(self, backend: FakeAdminBackend):
        self._backend = backend

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._backend.handle(request)


@pytest_asyncio.fixture
async def admin_bff_client():
    from httpx import ASGITransport, AsyncClient
    from frontend.main import app
    from frontend import http_client

    backend = FakeAdminBackend()
    fake_http = httpx.AsyncClient(transport=_FakeTransport(backend), base_url="http://backend")
    fresh_store = SessionStore()

    with patch.object(http_client, "_client", fake_http):
        with patch("frontend.routers.admin_guard.store", fresh_store):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                ac._fake_backend = backend  # type: ignore[attr-defined]
                ac._store = fresh_store  # type: ignore[attr-defined]
                yield ac


@pytest.mark.asyncio
async def test_usage_admin_route_denied_when_backend_recheck_not_admin(admin_bff_client):
    backend: FakeAdminBackend = admin_bff_client._fake_backend
    store: SessionStore = admin_bff_client._store
    store.add("sess-1", user_id="stale", email="stale@example.com", is_admin=True)
    backend.is_admin = False
    backend.authoritative_user_id = "fresh-user"

    resp = await admin_bff_client.get("/api/admin/usage/live", headers={"X-Session-Id": "sess-1"})
    assert resp.status_code == 403
    entry = store.get("sess-1")
    assert entry is not None
    assert entry.is_admin is False
    assert entry.user_id == "fresh-user"


@pytest.mark.asyncio
async def test_usage_admin_route_forwards_authoritative_actor_user_id(admin_bff_client):
    backend: FakeAdminBackend = admin_bff_client._fake_backend
    store: SessionStore = admin_bff_client._store
    store.add("sess-2", user_id="stale", email="stale@example.com", is_admin=True)
    backend.is_admin = True
    backend.authoritative_user_id = "real-admin"

    resp = await admin_bff_client.get("/api/admin/usage/live", headers={"X-Session-Id": "sess-2"})
    assert resp.status_code == 200
    assert resp.json()["actor_user_id"] == "real-admin"
    assert backend.last_actor_user_id == "real-admin"


@pytest.mark.asyncio
async def test_courses_admin_route_denied_when_backend_recheck_not_admin(admin_bff_client):
    backend: FakeAdminBackend = admin_bff_client._fake_backend
    store: SessionStore = admin_bff_client._store
    store.add("sess-3", user_id="stale", email="stale@example.com", is_admin=True)
    backend.is_admin = False

    resp = await admin_bff_client.post(
        "/api/courses",
        headers={"X-Session-Id": "sess-3", "Content-Type": "application/json"},
        content=json.dumps({"title": "Nope"}),
    )
    assert resp.status_code == 403
    assert backend.last_courses_user_id is None


@pytest.mark.asyncio
async def test_courses_admin_route_forwards_authoritative_user_id(admin_bff_client):
    backend: FakeAdminBackend = admin_bff_client._fake_backend
    store: SessionStore = admin_bff_client._store
    store.add("sess-4", user_id="stale", email="stale@example.com", is_admin=True)
    backend.is_admin = True
    backend.authoritative_user_id = "real-admin-2"

    resp = await admin_bff_client.post(
        "/api/courses",
        headers={"X-Session-Id": "sess-4", "Content-Type": "application/json"},
        content=json.dumps({"title": "Auth Course"}),
    )
    assert resp.status_code == 201
    assert resp.json()["forwarded_user_id"] == "real-admin-2"
    assert backend.last_courses_user_id == "real-admin-2"


@pytest.mark.asyncio
async def test_iam_admin_route_denied_when_backend_recheck_not_admin(admin_bff_client):
    backend: FakeAdminBackend = admin_bff_client._fake_backend
    store: SessionStore = admin_bff_client._store
    store.add("sess-iam-1", user_id="stale", email="stale@example.com", is_admin=True)
    backend.is_admin = False

    resp = await admin_bff_client.get("/api/admin/iam/users", headers={"X-Session-Id": "sess-iam-1"})
    assert resp.status_code == 403
    assert backend.last_iam_actor_user_id is None


@pytest.mark.asyncio
async def test_iam_admin_route_allowed_when_backend_recheck_confirms_admin(admin_bff_client):
    backend: FakeAdminBackend = admin_bff_client._fake_backend
    store: SessionStore = admin_bff_client._store
    store.add("sess-iam-2", user_id="stale", email="stale@example.com", is_admin=True)
    backend.is_admin = True
    backend.authoritative_user_id = "fresh-admin-id"

    resp = await admin_bff_client.get("/api/admin/iam/users", headers={"X-Session-Id": "sess-iam-2"})
    assert resp.status_code == 200
    assert backend.last_iam_actor_user_id == "fresh-admin-id"
    payload = resp.json()
    assert isinstance(payload["users"], list)


@pytest.mark.asyncio
async def test_iam_patch_forwards_authoritative_actor_user_id(admin_bff_client):
    backend: FakeAdminBackend = admin_bff_client._fake_backend
    store: SessionStore = admin_bff_client._store
    store.add("sess-iam-3", user_id="stale", email="stale@example.com", is_admin=True)
    backend.is_admin = True
    backend.authoritative_user_id = "actor-iam-123"
    backend.iam_patch_status = 200
    backend.iam_patch_payload = {
        "user": {
            "id": "target-55",
            "email": "target55@example.com",
            "display_name": None,
            "email_verified": True,
            "is_admin": False,
            "bootstrap_managed": True,
            "created_at": "2026-03-20 10:00:00",
        },
        "warning": "managed by bootstrap",
    }

    resp = await admin_bff_client.patch(
        "/api/admin/iam/users/target-55/admin",
        headers={"X-Session-Id": "sess-iam-3", "Content-Type": "application/json"},
        content=json.dumps({"is_admin": False}),
    )
    assert resp.status_code == 200
    assert backend.last_iam_target_user_id == "target-55"
    assert backend.last_iam_actor_user_id == "actor-iam-123"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "payload"),
    [
        (403, {"detail": "Admin access required"}),
        (404, {"detail": "User not found"}),
        (409, {"detail": "cannot_demote_last_admin"}),
    ],
)
async def test_iam_backend_error_details_propagate_unchanged(admin_bff_client, status, payload):
    backend: FakeAdminBackend = admin_bff_client._fake_backend
    store: SessionStore = admin_bff_client._store
    store.add("sess-iam-4", user_id="stale", email="stale@example.com", is_admin=True)
    backend.is_admin = True
    backend.authoritative_user_id = "actor-iam-456"
    backend.iam_patch_status = status
    backend.iam_patch_payload = payload

    resp = await admin_bff_client.patch(
        "/api/admin/iam/users/missing-user/admin",
        headers={"X-Session-Id": "sess-iam-4", "Content-Type": "application/json"},
        content=json.dumps({"is_admin": True}),
    )
    assert resp.status_code == status
    assert resp.json() == payload
