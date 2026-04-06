"""User preferences endpoints."""

from __future__ import annotations

from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..db import connection as db, models

router = APIRouter(prefix="/preferences", tags=["preferences"])

Conn = Annotated[asyncpg.Connection, Depends(db.get)]


class PreferencesResponse(BaseModel):
    prefs: dict


class PreferencesUpdate(BaseModel):
    prefs: dict


@router.get("", response_model=PreferencesResponse)
async def get_preferences(user_id: str, conn: Conn):
    prefs = await models.get_user_preferences(conn, user_id)
    return PreferencesResponse(prefs=prefs)


@router.put("", response_model=PreferencesResponse)
async def update_preferences(user_id: str, body: PreferencesUpdate, conn: Conn):
    existing = await models.get_user_preferences(conn, user_id)
    merged = {**existing, **body.prefs}
    await models.upsert_user_preferences(conn, user_id, merged)
    return PreferencesResponse(prefs=merged)
