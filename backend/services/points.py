"""
Points / gamification logic.

All public functions are async and accept an aiosqlite connection.
They are called from ws_session._save_state() at the end of every agent turn.

Scoring:
  section_advance  — 10 pts per section crossed (delta between old and new idx)
  lesson_complete  — 100 pts (deduplicated by unique index on point_events)
  daily_first      — 25 pts (first lesson activity of the UTC day)
  streak_bonus     — 10 × min(streak_days, 7) pts on days 2+
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import asyncpg

from ..db import models
from ..db.connection import ANON_USER_ID


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


async def award_section_advance(
    conn: asyncpg.Connection,
    user_id: str,
    enrollment_id: str,
    old_idx: int,
    new_idx: int,
) -> int:
    """Award 10 pts per section crossed. Returns total points awarded."""
    delta = max(0, new_idx - old_idx)
    if delta == 0:
        return 0
    points = delta * 10
    event_id = str(uuid.uuid4())
    inserted = await models.insert_point_event(
        conn, event_id, user_id, "section_advance", points, enrollment_id
    )
    if not inserted:
        return 0
    await models.upsert_user_points(
        conn, user_id, delta_points=points, delta_sections=delta
    )
    return points


async def award_lesson_complete(
    conn: asyncpg.Connection,
    user_id: str,
    enrollment_id: str,
) -> int:
    """Award 100 pts for completing a lesson. Idempotent — safe to call multiple times."""
    event_id = str(uuid.uuid4())
    inserted = await models.insert_point_event(
        conn, event_id, user_id, "lesson_complete", 100, enrollment_id
    )
    if not inserted:
        return 0
    await models.upsert_user_points(
        conn, user_id, delta_points=100, delta_lessons=1
    )
    return 100


async def award_daily_points_if_needed(
    conn: asyncpg.Connection,
    user_id: str,
) -> int:
    """
    Award first-lesson-of-day (25 pts) and streak bonus (10 × min(N, 7)) if
    today hasn't been recorded yet.  Returns total points awarded (0 if already done).
    """
    today = _today_utc()
    row = await models.get_user_points(conn, user_id)

    last_date: str | None = row["last_lesson_date"] if row else None
    if last_date == today:
        return 0  # already awarded today

    # Compute new streak
    current_streak = int(row["current_streak"]) if row else 0
    longest_streak = int(row["longest_streak"]) if row else 0

    if last_date is None:
        new_streak = 1
    else:
        from datetime import date, timedelta
        try:
            last = date.fromisoformat(last_date)
            diff = (date.fromisoformat(today) - last).days
            new_streak = current_streak + 1 if diff == 1 else 1
        except ValueError:
            new_streak = 1

    new_longest = max(longest_streak, new_streak)

    # First-of-day bonus
    daily_pts = 25
    event_id = str(uuid.uuid4())
    await models.insert_point_event(conn, event_id, user_id, "daily_first", daily_pts)

    # Streak bonus (only on day 2+)
    streak_pts = 0
    if new_streak >= 2:
        streak_pts = 10 * min(new_streak, 7)
        streak_event_id = str(uuid.uuid4())
        await models.insert_point_event(conn, streak_event_id, user_id, "streak_bonus", streak_pts)

    total = daily_pts + streak_pts
    await models.upsert_user_points(
        conn,
        user_id,
        delta_points=total,
        streak=new_streak,
        longest_streak=new_longest,
        last_lesson_date=today,
    )
    return total
