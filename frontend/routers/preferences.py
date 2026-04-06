"""User preferences REST proxy."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, Response

from ..http_client import get as get_http
from ..session_store import store

router = APIRouter(prefix="/preferences", tags=["preferences"])


def _require_session(session_id: str):
    entry = store.get(session_id)
    if entry is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return entry


@router.get("")
async def get_preferences(x_session_id: str = Header(...)):
    entry = _require_session(x_session_id)
    http = get_http()
    resp = await http.get("/preferences", params={"user_id": entry.user_id})
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@router.put("")
async def update_preferences(request: Request, x_session_id: str = Header(...)):
    entry = _require_session(x_session_id)
    http = get_http()
    body = await request.body()
    resp = await http.put(
        "/preferences",
        content=body,
        params={"user_id": entry.user_id},
        headers={"content-type": request.headers.get("content-type", "application/json")},
    )
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")
