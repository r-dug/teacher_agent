"""Shared authorization helpers for backend route handlers."""

from __future__ import annotations

import aiosqlite
from fastapi import HTTPException

from .db import models


async def require_admin(conn: aiosqlite.Connection, user_id: str) -> None:
    if not await models.get_user_is_admin(conn, user_id):
        raise HTTPException(status_code=403, detail="Admin access required")


def require_owner(owner_id: str, user_id: str) -> None:
    if owner_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")


async def require_admin_owner(
    conn: aiosqlite.Connection,
    owner_id: str,
    user_id: str,
) -> None:
    await require_admin(conn, user_id)
    require_owner(owner_id, user_id)
