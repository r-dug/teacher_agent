"""Course CRUD endpoints."""

from __future__ import annotations

from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import connection as db, models

router = APIRouter(prefix="/courses", tags=["courses"])

Conn = Annotated[aiosqlite.Connection, Depends(db.get)]


# ── Pydantic models ────────────────────────────────────────────────────────────

class CourseResponse(BaseModel):
    id: str
    user_id: str
    title: str
    description: str | None
    created_at: str
    updated_at: str


class CourseCreate(BaseModel):
    title: str
    description: str | None = None


class CourseUpdate(BaseModel):
    title: str | None = None
    description: str | None = None


# ── helpers ────────────────────────────────────────────────────────────────────

def _course_or_404(course: dict | None) -> dict:
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


def _check_ownership(course: dict, user_id: str) -> None:
    if course["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Access denied")


# ── routes ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[CourseResponse])
async def list_courses(user_id: str, conn: Conn):
    rows = await models.list_courses(conn, user_id)
    return [CourseResponse(**r) for r in rows]


@router.post("", response_model=CourseResponse, status_code=201)
async def create_course(user_id: str, body: CourseCreate, conn: Conn):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Title is required")
    course = await models.create_course(conn, user_id, title, body.description)
    return CourseResponse(**course)


@router.get("/{course_id}", response_model=CourseResponse)
async def get_course(course_id: str, user_id: str, conn: Conn):
    course = _course_or_404(await models.get_course(conn, course_id))
    _check_ownership(course, user_id)
    return CourseResponse(**course)


@router.patch("/{course_id}", response_model=CourseResponse)
async def update_course(course_id: str, user_id: str, body: CourseUpdate, conn: Conn):
    course = _course_or_404(await models.get_course(conn, course_id))
    _check_ownership(course, user_id)
    updates: dict = {}
    if body.title is not None:
        updates["title"] = body.title.strip() or course["title"]
    if body.description is not None:
        updates["description"] = body.description
    if updates:
        await models.update_course(conn, course_id, **updates)
    course = await models.get_course(conn, course_id)
    return CourseResponse(**course)  # type: ignore[arg-type]


@router.delete("/{course_id}", status_code=204)
async def delete_course(course_id: str, user_id: str, conn: Conn):
    course = _course_or_404(await models.get_course(conn, course_id))
    _check_ownership(course, user_id)
    await models.delete_course(conn, course_id)
