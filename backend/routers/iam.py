"""Admin IAM management endpoints."""

from __future__ import annotations

from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..authz import require_admin
from ..config import settings
from ..db import connection as db, models

router = APIRouter(prefix="/admin/iam", tags=["iam"])

Conn = Annotated[aiosqlite.Connection, Depends(db.get)]

ERR_CANNOT_DEMOTE_SELF = "cannot_demote_self"
ERR_CANNOT_DEMOTE_LAST_ADMIN = "cannot_demote_last_admin"
WARN_BOOTSTRAP_MANAGED = (
    "This admin is managed by ADMIN_EMAILS and may be re-granted on service restart."
)


class IamUserResponse(BaseModel):
    id: str
    email: str | None = None
    display_name: str | None = None
    email_verified: bool
    is_admin: bool
    bootstrap_managed: bool
    created_at: str


class IamUsersListResponse(BaseModel):
    users: list[IamUserResponse]


class IamSetAdminRequest(BaseModel):
    is_admin: bool


class IamSetAdminResponse(BaseModel):
    user: IamUserResponse
    warning: str | None = None


def _is_bootstrap_managed_email(email: str | None) -> bool:
    if not email:
        return False
    return email.lower().strip() in set(settings.ADMIN_EMAILS)


def _to_iam_user(row: dict) -> IamUserResponse:
    email = row.get("email")
    return IamUserResponse(
        id=str(row["id"]),
        email=str(email) if email is not None else None,
        display_name=(str(row["display_name"]) if row.get("display_name") is not None else None),
        email_verified=bool(row.get("email_verified", 0)),
        is_admin=bool(row.get("is_admin", 0)),
        bootstrap_managed=_is_bootstrap_managed_email(str(email) if email is not None else None),
        created_at=str(row["created_at"]),
    )


@router.get("/users", response_model=IamUsersListResponse)
async def list_iam_users(actor_user_id: str, conn: Conn):
    await require_admin(conn, actor_user_id)
    rows = await models.list_users_for_admin_iam(conn)
    return IamUsersListResponse(users=[_to_iam_user(r) for r in rows])


@router.patch("/users/{target_user_id}/admin", response_model=IamSetAdminResponse)
async def set_user_admin(
    target_user_id: str,
    body: IamSetAdminRequest,
    actor_user_id: str,
    conn: Conn,
):
    await require_admin(conn, actor_user_id)
    target = await models.get_user_by_id(conn, target_user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    target_is_admin = bool(target.get("is_admin", 0))
    if target_is_admin and not body.is_admin:
        admin_count = await models.count_admin_users(conn)
        if admin_count <= 1:
            raise HTTPException(status_code=409, detail=ERR_CANNOT_DEMOTE_LAST_ADMIN)
        if target_user_id == actor_user_id:
            raise HTTPException(status_code=409, detail=ERR_CANNOT_DEMOTE_SELF)

    if target_is_admin != body.is_admin:
        await models.set_user_admin(conn, target_user_id, is_admin=body.is_admin)
        target = await models.get_user_by_id(conn, target_user_id)
        assert target is not None

    user = _to_iam_user(target)
    warning: str | None = None
    if not body.is_admin and user.bootstrap_managed:
        warning = WARN_BOOTSTRAP_MANAGED

    return IamSetAdminResponse(user=user, warning=warning)
