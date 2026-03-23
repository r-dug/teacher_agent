"""Usage proxy — forwards to backend.  Admin endpoints require is_admin flag."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, Response

from ..http_client import get as get_http
from ..session_store import store
from .admin_guard import require_admin_session

router = APIRouter(tags=["usage"])


def _require_session(x_session_id: str) -> None:
    entry = store.get(x_session_id)
    if entry is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")


# ── Session-scoped ─────────────────────────────────────────────────────────────

@router.get("/usage")
async def get_usage(x_session_id: str = Header(...)):
    _require_session(x_session_id)
    http = get_http()
    resp = await http.get("/usage")
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@router.delete("/usage")
async def reset_usage(x_session_id: str = Header(...)):
    _require_session(x_session_id)
    http = get_http()
    resp = await http.delete("/usage")
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


# ── Admin endpoints ────────────────────────────────────────────────────────────

@router.get("/admin/usage/live")
async def usage_live(x_session_id: str = Header(...)):
    entry = await require_admin_session(x_session_id)
    http = get_http()
    resp = await http.get("/admin/usage/live", params={"actor_user_id": entry.user_id})
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@router.get("/admin/usage/series")
async def usage_series(request: Request, x_session_id: str = Header(...)):
    entry = await require_admin_session(x_session_id)
    http = get_http()
    params = dict(request.query_params)
    params["actor_user_id"] = entry.user_id
    resp = await http.get("/admin/usage/series", params=params)
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@router.get("/admin/usage/totals")
async def usage_totals(request: Request, x_session_id: str = Header(...)):
    entry = await require_admin_session(x_session_id)
    http = get_http()
    params = dict(request.query_params)
    params["actor_user_id"] = entry.user_id
    resp = await http.get("/admin/usage/totals", params=params)
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@router.get("/admin/usage/users")
async def usage_users(x_session_id: str = Header(...)):
    entry = await require_admin_session(x_session_id)
    http = get_http()
    resp = await http.get("/admin/usage/users", params={"actor_user_id": entry.user_id})
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")
