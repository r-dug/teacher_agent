"""Usage tracking endpoints — some admin-only, some session-scoped."""

from __future__ import annotations

import asyncio
import time
from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, Query

from ..authz import require_admin
from ..app_state import app_state
from ..db import connection as db

router = APIRouter(tags=["usage"])

Conn = Annotated[asyncpg.Connection, Depends(db.get)]


# ── Session-scoped (in-memory, powers the sidebar widget) ────────────────────

@router.get("/usage")
async def get_usage():
    """In-memory aggregates since last reset.  Powers the sidebar TokenUsageDisplay."""
    return app_state.token_tracker.summary()


@router.delete("/usage")
async def reset_usage():
    app_state.token_tracker.reset()
    return {"status": "reset"}


# ── Admin endpoints ────────────────────────────────────────────────────────────
# Backend authorization is required even for BFF-proxied traffic.

@router.get("/admin/usage/live")
async def usage_live(actor_user_id: str, conn: Conn):
    """Raw events from the last 90 s — for the 1-second live feed."""
    await require_admin(conn, actor_user_id)
    rows = await asyncio.to_thread(app_state.token_tracker.query_live)
    return {"events": rows}


@router.get("/admin/usage/series")
async def usage_series(
    actor_user_id: str,
    from_ts: float = Query(default=0),
    to_ts: float = Query(default=0),
    granularity: str = Query(default="minute"),   # 'minute' | 'hour'
    user_id: str | None = Query(default=None),
    conn: Conn = None,  # type: ignore[assignment]
):
    """
    Time-series rows for charts.  Returns usage_minutes or usage_hours rows
    covering [from_ts, to_ts).  If to_ts=0, uses now.
    """
    await require_admin(conn, actor_user_id)
    if to_ts == 0:
        to_ts = time.time()
    rows = await asyncio.to_thread(
        app_state.token_tracker.query_series,
        from_ts, to_ts, granularity, user_id,
    )
    return {"rows": rows, "from_ts": from_ts, "to_ts": to_ts, "granularity": granularity}


@router.get("/admin/usage/totals")
async def usage_totals(
    actor_user_id: str,
    window: str = Query(default="today"),   # 'today'|'week'|'month'|'all'
    user_id: str | None = Query(default=None),
    conn: Conn = None,  # type: ignore[assignment]
):
    """Aggregated totals for summary cards."""
    await require_admin(conn, actor_user_id)
    now = time.time()
    windows = {
        "today": 86400,
        "week":  7 * 86400,
        "month": 30 * 86400,
        "all":   None,
    }
    window_seconds = windows.get(window)
    totals = await asyncio.to_thread(
        app_state.token_tracker.query_totals,
        window_seconds, user_id,
    )
    return {"window": window, "totals": totals}


@router.get("/admin/usage/users")
async def usage_users(actor_user_id: str, conn: Conn):
    """List all users with basic info (for the per-user breakdown filter)."""
    await require_admin(conn, actor_user_id)
    records = await conn.fetch(
        "SELECT id, email, display_name, is_admin, created_at FROM users ORDER BY created_at"
    )
    return {"users": [dict(r) for r in records]}
