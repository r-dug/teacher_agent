"""Admin persona management endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..authz import require_admin
from ..db import connection as db, models

router = APIRouter(prefix="/admin/personas", tags=["admin-personas"])

Conn = Annotated[asyncpg.Connection, Depends(db.get)]

_MAX_PERSONA_INSTRUCTIONS = 5_000
_MAX_VOICE_INSTRUCTIONS = 500


class AdminPersonaCreate(BaseModel):
    id: str
    name: str
    instructions: str
    voice_instructions: str = ""


class AdminPersonaUpdate(BaseModel):
    name: str
    instructions: str
    voice_instructions: str = ""


class AdminPersonaResponse(BaseModel):
    id: str
    name: str
    instructions: str
    voice_instructions: str
    user_id: str | None
    created_at: datetime


@router.get("", response_model=list[AdminPersonaResponse])
async def list_personas(actor_user_id: str, conn: Conn):
    await require_admin(conn, actor_user_id)
    rows = await models.list_all_personas(conn)
    return [AdminPersonaResponse(**r) for r in rows]


@router.post("", response_model=AdminPersonaResponse, status_code=201)
async def create_persona(body: AdminPersonaCreate, actor_user_id: str, conn: Conn):
    await require_admin(conn, actor_user_id)
    if len(body.instructions) > _MAX_PERSONA_INSTRUCTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Persona instructions must be {_MAX_PERSONA_INSTRUCTIONS} characters or fewer",
        )
    if len(body.voice_instructions) > _MAX_VOICE_INSTRUCTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Voice instructions must be {_MAX_VOICE_INSTRUCTIONS} characters or fewer",
        )
    row = await models.create_persona(
        conn,
        persona_id=body.id,
        user_id=None,
        name=body.name,
        instructions=body.instructions,
        voice_instructions=body.voice_instructions,
    )
    return AdminPersonaResponse(**row)


@router.patch("/{persona_id}", response_model=AdminPersonaResponse)
async def update_persona(persona_id: str, body: AdminPersonaUpdate, actor_user_id: str, conn: Conn):
    await require_admin(conn, actor_user_id)
    if len(body.instructions) > _MAX_PERSONA_INSTRUCTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Persona instructions must be {_MAX_PERSONA_INSTRUCTIONS} characters or fewer",
        )
    if len(body.voice_instructions) > _MAX_VOICE_INSTRUCTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Voice instructions must be {_MAX_VOICE_INSTRUCTIONS} characters or fewer",
        )
    row = await models.update_persona(
        conn, persona_id, body.name, body.instructions,
        voice_instructions=body.voice_instructions,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Persona not found")
    return AdminPersonaResponse(**row)


@router.delete("/{persona_id}", status_code=204)
async def delete_persona(persona_id: str, actor_user_id: str, conn: Conn):
    await require_admin(conn, actor_user_id)
    deleted = await models.admin_delete_persona(conn, persona_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Persona not found")
