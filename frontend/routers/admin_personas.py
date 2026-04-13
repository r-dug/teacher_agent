"""Admin personas proxy — forwards to backend admin persona endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Header, Request, Response

from ..http_client import get as get_http
from .admin_guard import require_admin_session

router = APIRouter(tags=["admin-personas"])


@router.get("/admin/personas")
async def list_personas(x_session_id: str = Header(...)):
    entry = await require_admin_session(x_session_id)
    http = get_http()
    resp = await http.get("/admin/personas", params={"actor_user_id": entry.user_id})
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@router.post("/admin/personas", status_code=201)
async def create_persona(request: Request, x_session_id: str = Header(...)):
    entry = await require_admin_session(x_session_id)
    http = get_http()
    body = await request.body()
    resp = await http.post(
        "/admin/personas",
        content=body,
        params={"actor_user_id": entry.user_id},
        headers={"content-type": request.headers.get("content-type", "application/json")},
    )
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@router.patch("/admin/personas/{persona_id}")
async def update_persona(persona_id: str, request: Request, x_session_id: str = Header(...)):
    entry = await require_admin_session(x_session_id)
    http = get_http()
    body = await request.body()
    resp = await http.patch(
        f"/admin/personas/{persona_id}",
        content=body,
        params={"actor_user_id": entry.user_id},
        headers={"content-type": request.headers.get("content-type", "application/json")},
    )
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@router.delete("/admin/personas/{persona_id}", status_code=204)
async def delete_persona(persona_id: str, x_session_id: str = Header(...)):
    entry = await require_admin_session(x_session_id)
    http = get_http()
    resp = await http.delete(
        f"/admin/personas/{persona_id}",
        params={"actor_user_id": entry.user_id},
    )
    return Response(content=resp.content, status_code=resp.status_code)
