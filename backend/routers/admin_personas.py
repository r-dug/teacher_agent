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
_MAX_PREP_PROMPT = 2_000
_VALID_FORMATS = {"", "mp3", "opus", "aac", "flac", "wav", "pcm16"}


class AdminPersonaCreate(BaseModel):
    id: str
    name: str
    instructions: str
    voice_instructions: str = ""
    tts_voice: str = ""
    tts_speed: float = 1.0
    tts_format: str = ""
    tts_prep_prompt: str = ""


class AdminPersonaUpdate(BaseModel):
    name: str
    instructions: str
    voice_instructions: str = ""
    tts_voice: str = ""
    tts_speed: float = 1.0
    tts_format: str = ""
    tts_prep_prompt: str = ""


class AdminPersonaResponse(BaseModel):
    id: str
    name: str
    instructions: str
    voice_instructions: str
    tts_voice: str
    tts_speed: float
    tts_format: str
    tts_prep_prompt: str
    user_id: str | None
    created_at: datetime


@router.get("", response_model=list[AdminPersonaResponse])
async def list_personas(actor_user_id: str, conn: Conn):
    await require_admin(conn, actor_user_id)
    rows = await models.list_all_personas(conn)
    return [AdminPersonaResponse(**r) for r in rows]


def _validate_persona_body(body: AdminPersonaCreate | AdminPersonaUpdate) -> None:
    if len(body.instructions) > _MAX_PERSONA_INSTRUCTIONS:
        raise HTTPException(422, f"System prompt must be {_MAX_PERSONA_INSTRUCTIONS} characters or fewer")
    if len(body.voice_instructions) > _MAX_VOICE_INSTRUCTIONS:
        raise HTTPException(422, f"Voice instructions must be {_MAX_VOICE_INSTRUCTIONS} characters or fewer")
    if len(body.tts_prep_prompt) > _MAX_PREP_PROMPT:
        raise HTTPException(422, f"TTS prep prompt must be {_MAX_PREP_PROMPT} characters or fewer")
    if body.tts_format and body.tts_format not in _VALID_FORMATS:
        raise HTTPException(422, f"Invalid TTS format. Supported: {', '.join(sorted(_VALID_FORMATS - {''}))}")
    if not (0.25 <= body.tts_speed <= 4.0):
        raise HTTPException(422, "TTS speed must be between 0.25 and 4.0")


def _persona_kwargs(body: AdminPersonaCreate | AdminPersonaUpdate) -> dict:
    return dict(
        name=body.name,
        instructions=body.instructions,
        voice_instructions=body.voice_instructions,
        tts_voice=body.tts_voice,
        tts_speed=body.tts_speed,
        tts_format=body.tts_format,
        tts_prep_prompt=body.tts_prep_prompt,
    )


@router.post("", response_model=AdminPersonaResponse, status_code=201)
async def create_persona(body: AdminPersonaCreate, actor_user_id: str, conn: Conn):
    await require_admin(conn, actor_user_id)
    _validate_persona_body(body)
    row = await models.create_persona(
        conn, persona_id=body.id, user_id=None, **_persona_kwargs(body),
    )
    return AdminPersonaResponse(**row)


@router.patch("/{persona_id}", response_model=AdminPersonaResponse)
async def update_persona(persona_id: str, body: AdminPersonaUpdate, actor_user_id: str, conn: Conn):
    await require_admin(conn, actor_user_id)
    _validate_persona_body(body)
    row = await models.update_persona(conn, persona_id, **_persona_kwargs(body))
    if row is None:
        raise HTTPException(status_code=404, detail="Persona not found")
    return AdminPersonaResponse(**row)


@router.delete("/{persona_id}", status_code=204)
async def delete_persona(persona_id: str, actor_user_id: str, conn: Conn):
    await require_admin(conn, actor_user_id)
    deleted = await models.admin_delete_persona(conn, persona_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Persona not found")
