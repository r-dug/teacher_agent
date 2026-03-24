"""Integration tests for admin IAM and admin auth hardening."""

from __future__ import annotations

import pytest

from backend.app_state import app_state
from backend.config import settings
from backend.db import models
from backend.routers.iam import (
    ERR_CANNOT_DEMOTE_LAST_ADMIN,
    ERR_CANNOT_DEMOTE_SELF,
    WARN_BOOTSTRAP_MANAGED,
)


async def _create_user(mem_db, email: str, *, is_admin: bool = False) -> dict:
    user = await models.create_user(mem_db, email, "pw")
    if is_admin:
        await mem_db.execute("UPDATE users SET is_admin = 1 WHERE id = $1", user["id"])
    return user


@pytest.mark.asyncio
async def test_admin_usage_endpoints_require_admin_and_allow_admin(client, mem_db, monkeypatch):
    user = await _create_user(mem_db, "usage-user@example.com")
    admin = await _create_user(mem_db, "usage-admin@example.com", is_admin=True)

    monkeypatch.setattr(app_state.token_tracker, "query_live", lambda: [])
    monkeypatch.setattr(app_state.token_tracker, "query_series", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(app_state.token_tracker, "query_totals", lambda *_args, **_kwargs: {"calls": 0})

    non_admin_calls = [
        ("/admin/usage/live", {"actor_user_id": user["id"]}),
        ("/admin/usage/series", {"actor_user_id": user["id"], "from_ts": "0", "to_ts": "0"}),
        ("/admin/usage/totals", {"actor_user_id": user["id"], "window": "today"}),
        ("/admin/usage/users", {"actor_user_id": user["id"]}),
    ]
    for path, params in non_admin_calls:
        resp = await client.get(path, params=params)
        assert resp.status_code == 403

    admin_calls = [
        ("/admin/usage/live", {"actor_user_id": admin["id"]}),
        (
            "/admin/usage/series",
            {
                "actor_user_id": admin["id"],
                "from_ts": "0",
                "to_ts": "0",
                "granularity": "minute",
                "user_id": user["id"],
            },
        ),
        ("/admin/usage/totals", {"actor_user_id": admin["id"], "window": "today", "user_id": user["id"]}),
        ("/admin/usage/users", {"actor_user_id": admin["id"]}),
    ]
    for path, params in admin_calls:
        resp = await client.get(path, params=params)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_iam_non_admin_actor_gets_403_on_list_and_update(client, mem_db):
    non_admin = await _create_user(mem_db, "member@example.com")
    target = await _create_user(mem_db, "target@example.com", is_admin=False)

    list_resp = await client.get("/admin/iam/users", params={"actor_user_id": non_admin["id"]})
    assert list_resp.status_code == 403
    assert list_resp.json()["detail"] == "Admin access required"

    patch_resp = await client.patch(
        f"/admin/iam/users/{target['id']}/admin",
        params={"actor_user_id": non_admin["id"]},
        json={"is_admin": True},
    )
    assert patch_resp.status_code == 403
    assert patch_resp.json()["detail"] == "Admin access required"


@pytest.mark.asyncio
async def test_iam_update_missing_target_returns_404(client, mem_db):
    admin = await _create_user(mem_db, "iam-admin-missing@example.com", is_admin=True)

    resp = await client.patch(
        "/admin/iam/users/does-not-exist/admin",
        params={"actor_user_id": admin["id"]},
        json={"is_admin": True},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "User not found"


@pytest.mark.asyncio
async def test_iam_admin_list_includes_bootstrap_managed(client, mem_db, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_EMAILS", ("bootstrap-admin@example.com",))
    admin = await _create_user(mem_db, "real-admin@example.com", is_admin=True)
    bootstrap = await _create_user(mem_db, "Bootstrap-Admin@Example.com")
    normal = await _create_user(mem_db, "normal-user@example.com")

    resp = await client.get("/admin/iam/users", params={"actor_user_id": admin["id"]})
    assert resp.status_code == 200
    rows = {row["id"]: row for row in resp.json()["users"]}
    assert rows[bootstrap["id"]]["bootstrap_managed"] is True
    assert rows[normal["id"]]["bootstrap_managed"] is False


@pytest.mark.asyncio
async def test_iam_promote_succeeds_and_persists(client, mem_db):
    admin = await _create_user(mem_db, "promoter@example.com", is_admin=True)
    target = await _create_user(mem_db, "promote-target@example.com")

    resp = await client.patch(
        f"/admin/iam/users/{target['id']}/admin",
        params={"actor_user_id": admin["id"]},
        json={"is_admin": True},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["user"]["is_admin"] is True
    assert payload.get("warning") is None

    updated = await models.get_user_by_id(mem_db, target["id"])
    assert updated is not None
    assert updated["is_admin"] == 1


@pytest.mark.asyncio
async def test_iam_self_demotion_returns_409(client, mem_db):
    actor = await _create_user(mem_db, "self-demote@example.com", is_admin=True)
    await _create_user(mem_db, "second-admin@example.com", is_admin=True)

    resp = await client.patch(
        f"/admin/iam/users/{actor['id']}/admin",
        params={"actor_user_id": actor["id"]},
        json={"is_admin": False},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == ERR_CANNOT_DEMOTE_SELF


@pytest.mark.asyncio
async def test_iam_last_admin_demotion_returns_409(client, mem_db):
    only_admin = await _create_user(mem_db, "last-admin@example.com", is_admin=True)

    resp = await client.patch(
        f"/admin/iam/users/{only_admin['id']}/admin",
        params={"actor_user_id": only_admin["id"]},
        json={"is_admin": False},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == ERR_CANNOT_DEMOTE_LAST_ADMIN


@pytest.mark.asyncio
async def test_iam_demote_bootstrap_managed_admin_succeeds_with_warning(client, mem_db, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_EMAILS", ("bootstrap-admin@example.com",))
    actor = await _create_user(mem_db, "actor-admin@example.com", is_admin=True)
    bootstrap_admin = await _create_user(mem_db, "bootstrap-admin@example.com", is_admin=True)

    resp = await client.patch(
        f"/admin/iam/users/{bootstrap_admin['id']}/admin",
        params={"actor_user_id": actor["id"]},
        json={"is_admin": False},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["user"]["is_admin"] is False
    assert payload["user"]["bootstrap_managed"] is True
    assert payload["warning"] == WARN_BOOTSTRAP_MANAGED

    updated = await models.get_user_by_id(mem_db, bootstrap_admin["id"])
    assert updated is not None
    assert updated["is_admin"] == 0


@pytest.mark.asyncio
async def test_demoted_admin_cannot_update_or_delete_templates(client, mem_db):
    admin = await _create_user(mem_db, "demote-admin@example.com", is_admin=True)
    course = await models.create_course(mem_db, admin["id"], "Admin Course", "desc")
    lesson1 = await models.create_lesson(mem_db, admin["id"], "Admin Lesson 1", course_id=course["id"])
    lesson2 = await models.create_lesson(mem_db, admin["id"], "Admin Lesson 2", course_id=course["id"])

    # Sanity check: active admin can update template resources.
    can_update_course = await client.patch(
        f"/courses/{course['id']}",
        params={"user_id": admin["id"]},
        json={"title": "Updated Title"},
    )
    assert can_update_course.status_code == 200

    can_update_lesson = await client.patch(
        f"/lessons/{lesson1}",
        params={"user_id": admin["id"]},
        json={"title": "Updated Lesson"},
    )
    assert can_update_lesson.status_code == 200

    # Demote and verify write access is revoked immediately.
    await mem_db.execute("UPDATE users SET is_admin = 0 WHERE id = $1", admin["id"])

    cannot_update_course = await client.patch(
        f"/courses/{course['id']}",
        params={"user_id": admin["id"]},
        json={"title": "Should Fail"},
    )
    assert cannot_update_course.status_code == 403

    cannot_delete_lesson = await client.delete(
        f"/lessons/{lesson2}",
        params={"user_id": admin["id"]},
    )
    assert cannot_delete_lesson.status_code == 403
