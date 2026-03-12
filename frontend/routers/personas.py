"""Persona REST proxy."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, Response

from ..http_client import get as get_http
from ..session_store import store

router = APIRouter(prefix="/personas", tags=["personas"])


def _require_session(session_id: str):
    entry = store.get(session_id)
    if entry is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return entry


@router.get("")
async def list_personas(x_session_id: str = Header(...)):
    entry = _require_session(x_session_id)
    http = get_http()
    resp = await http.get("/personas", params={"user_id": entry.user_id})
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@router.post("", status_code=201)
async def create_persona(request: Request, x_session_id: str = Header(...)):
    entry = _require_session(x_session_id)
    http = get_http()
    body = await request.body()
    resp = await http.post(
        "/personas",
        content=body,
        params={"user_id": entry.user_id},
        headers={"content-type": request.headers.get("content-type", "application/json")},
    )
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/json")


@router.delete("/{persona_id}", status_code=204)
async def delete_persona(persona_id: str, x_session_id: str = Header(...)):
    entry = _require_session(x_session_id)
    http = get_http()
    resp = await http.delete(f"/personas/{persona_id}", params={"user_id": entry.user_id})
    return Response(content=resp.content, status_code=resp.status_code)
