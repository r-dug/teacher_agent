"""
Leaderboard endpoints.

GET /leaderboard           — top 50 by total_points (session-gated via user_id query param)
GET /leaderboard/me        — caller's own stats + rank
"""

from __future__ import annotations

from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..db import connection as db, models

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])

Conn = Annotated[aiosqlite.Connection, Depends(db.get)]


class LeaderboardEntry(BaseModel):
    rank: int
    username: str
    total_points: int
    lessons_completed: int
    sections_advanced: int
    current_streak: int
    longest_streak: int
    is_self: bool = False


class LeaderboardResponse(BaseModel):
    entries: list[LeaderboardEntry]
    self_rank: int | None = None
    self_points: int | None = None


class UserStatsResponse(BaseModel):
    username: str
    total_points: int
    lessons_completed: int
    sections_advanced: int
    current_streak: int
    longest_streak: int
    rank: int | None


@router.get("", response_model=LeaderboardResponse)
async def get_leaderboard(
    conn: Conn,
    user_id: str = Query(default=""),
    limit: int = Query(default=50, le=100),
):
    rows = await models.get_leaderboard(conn, limit=limit)
    entries = [
        LeaderboardEntry(
            rank=int(r["rank"]),
            username=r["username"] or r.get("display_name") or "unknown",
            total_points=int(r["total_points"]),
            lessons_completed=int(r["lessons_completed"]),
            sections_advanced=int(r["sections_advanced"]),
            current_streak=int(r["current_streak"]),
            longest_streak=int(r["longest_streak"]),
            is_self=(r.get("user_id") == user_id) if user_id else False,
        )
        for r in rows
    ]

    self_rank = None
    self_points = None
    if user_id:
        self_rank = await models.get_user_rank(conn, user_id)
        pts = await models.get_user_points(conn, user_id)
        self_points = int(pts["total_points"]) if pts else 0

    return LeaderboardResponse(entries=entries, self_rank=self_rank, self_points=self_points)


@router.get("/me", response_model=UserStatsResponse)
async def get_my_stats(conn: Conn, user_id: str = Query(...)):
    pts = await models.get_user_points(conn, user_id)
    user = await models.get_user_by_id(conn, user_id)
    rank = await models.get_user_rank(conn, user_id)
    username = (user.get("username") or user.get("display_name") or "unknown") if user else "unknown"
    if pts is None:
        return UserStatsResponse(
            username=username,
            total_points=0, lessons_completed=0, sections_advanced=0,
            current_streak=0, longest_streak=0, rank=None,
        )
    return UserStatsResponse(
        username=username,
        total_points=int(pts["total_points"]),
        lessons_completed=int(pts["lessons_completed"]),
        sections_advanced=int(pts["sections_advanced"]),
        current_streak=int(pts["current_streak"]),
        longest_streak=int(pts["longest_streak"]),
        rank=rank,
    )
