"""IAM admin proxy routes."""

from __future__ import annotations

from fastapi import APIRouter, Header, Request, Response

from ..http_client import get as get_http
from .admin_guard import require_admin_session

router = APIRouter(prefix="/admin/iam", tags=["iam"])


@router.get("/users")
async def list_iam_users(x_session_id: str = Header(...)):
    entry = await require_admin_session(x_session_id)
    http = get_http()
    resp = await http.get(
        "/admin/iam/users",
        params={"actor_user_id": entry.user_id},
    )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


@router.patch("/users/{target_user_id}/admin")
async def set_user_admin(
    target_user_id: str,
    request: Request,
    x_session_id: str = Header(...),
):
    entry = await require_admin_session(x_session_id)
    http = get_http()
    body = await request.body()
    resp = await http.patch(
        f"/admin/iam/users/{target_user_id}/admin",
        content=body,
        headers={"content-type": request.headers.get("content-type", "application/json")},
        params={"actor_user_id": entry.user_id},
    )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )
