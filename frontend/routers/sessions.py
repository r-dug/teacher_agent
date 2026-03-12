"""Session lifecycle and upload token endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..http_client import get as get_http
from ..rate_limiter import limiter
from ..session_store import store

router = APIRouter(tags=["sessions"])


# ── models ─────────────────────────────────────────────────────────────────────

class SessionResponse(BaseModel):
    session_id: str


class UploadTokenResponse(BaseModel):
    token: str


# ── helpers ────────────────────────────────────────────────────────────────────

def _require_session(session_id: str):
    entry = store.get(session_id)
    if entry is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return entry


# ── routes ─────────────────────────────────────────────────────────────────────

@router.post("/sessions", response_model=SessionResponse, status_code=201)
async def create_session():
    """
    Create a new session.  Calls the backend to persist the session record,
    then caches it in the frontend store for fast subsequent validation.
    """
    http = get_http()
    resp = await http.post("/internal/sessions", json={})
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Backend session creation failed")
    data = resp.json()
    session_id: str = data["session_id"]
    store.add(session_id, user_id=data.get("user_id", ""))
    return SessionResponse(session_id=session_id)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str):
    _require_session(session_id)
    http = get_http()
    await http.delete(f"/internal/sessions/{session_id}")
    store.remove(session_id)
    limiter.remove(session_id)


@router.get("/sessions/{session_id}/upload_token", response_model=UploadTokenResponse)
async def get_upload_token(session_id: str):
    """
    Issue a short-lived token the client can use to upload a PDF directly
    to the backend's /lessons/decompose endpoint.
    """
    entry = _require_session(session_id)

    if not limiter.allow(session_id, tokens=2.0):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    http = get_http()
    resp = await http.post(
        "/internal/upload_tokens",
        json={"session_id": session_id, "ttl_seconds": 300},
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Backend token creation failed")

    return UploadTokenResponse(token=resp.json()["token"])
