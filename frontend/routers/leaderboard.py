"""BFF proxy for leaderboard endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from ..http_client import get as get_http
from ..session_store import store

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


def _require_session(x_session_id: str) -> str:
    entry = store.get(x_session_id)
    if entry is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return entry.user_id


@router.get("")
async def get_leaderboard(
    limit: int = Query(default=50, le=100),
    x_session_id: str = Header(...),
):
    user_id = _require_session(x_session_id)
    http = get_http()
    resp = await http.get("/leaderboard", params={"user_id": user_id, "limit": limit})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@router.get("/me")
async def get_my_stats(x_session_id: str = Header(...)):
    user_id = _require_session(x_session_id)
    http = get_http()
    resp = await http.get("/leaderboard/me", params={"user_id": user_id})
    return JSONResponse(status_code=resp.status_code, content=resp.json())
