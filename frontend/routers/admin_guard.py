"""Shared admin-session revalidation for BFF admin routes."""

from __future__ import annotations

from fastapi import HTTPException

from ..http_client import get as get_http
from ..session_store import SessionEntry, store


async def require_admin_session(session_id: str) -> SessionEntry:
    entry = store.get(session_id)
    if entry is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    http = get_http()
    resp = await http.get(f"/internal/auth/user-by-session/{session_id}")
    if not resp.is_success:
        store.remove(session_id)
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    user = resp.json()
    entry.user_id = str(user.get("user_id") or "")
    entry.email = str(user.get("email") or "")
    entry.is_admin = bool(user.get("is_admin", False))

    if not entry.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    return entry
