"""Terms of Service proxy (BFF layer).

`GET  /api/terms`         — public; returns current TOS text + version.
`POST /api/terms/accept`  — session required; records the current version
                            against the calling user's account.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Response

from ..http_client import get as get_http
from ..session_store import store

router = APIRouter(prefix="/terms", tags=["terms"])


@router.get("")
async def get_terms():
    """Fetch the current TOS text + version from the backend (public)."""
    http = get_http()
    resp = await http.get("/internal/terms")
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type="application/json",
    )


@router.post("/accept", status_code=204)
async def accept_terms(x_session_id: str = Header(...)):
    """Record that the calling user has accepted the current TOS version."""
    entry = store.get(x_session_id)
    if entry is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    http = get_http()
    resp = await http.post("/internal/terms/accept", json={"user_id": entry.user_id})
    if resp.status_code != 204:
        raise HTTPException(status_code=502, detail="Failed to record acceptance")

    # Also fetch the current version so we can update the in-memory session
    # store — otherwise /api/auth/me would keep reporting the stale value until
    # the BFF restarts.
    try:
        terms_resp = await http.get("/internal/terms")
        if terms_resp.is_success:
            entry.terms_version_accepted = terms_resp.json().get("version", "")
    except Exception:
        # Best-effort: even if the refresh fails, the DB write has succeeded,
        # and the next /me will pick it up.
        pass
