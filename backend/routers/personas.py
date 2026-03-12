"""Persona CRUD endpoints."""

from __future__ import annotations

from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import connection as db, models

router = APIRouter(prefix="/personas", tags=["personas"])

Conn = Annotated[aiosqlite.Connection, Depends(db.get)]


class PersonaCreate(BaseModel):
    id: str          # caller-chosen slug, e.g. 'my-tutor'
    name: str
    instructions: str


class PersonaResponse(BaseModel):
    id: str
    name: str
    instructions: str
    user_id: str | None
    created_at: str


@router.get("", response_model=list[PersonaResponse])
async def list_personas(user_id: str | None = None, conn: Conn = None):
    rows = await models.get_personas(conn, user_id)
    return [PersonaResponse(**r) for r in rows]


@router.post("", response_model=PersonaResponse, status_code=201)
async def create_persona(body: PersonaCreate, user_id: str, conn: Conn):
    row = await models.create_persona(
        conn,
        persona_id=body.id,
        user_id=user_id,
        name=body.name,
        instructions=body.instructions,
    )
    return PersonaResponse(**row)


@router.delete("/{persona_id}", status_code=204)
async def delete_persona(persona_id: str, user_id: str, conn: Conn):
    deleted = await models.delete_persona(conn, persona_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Persona not found or not owned by user")
