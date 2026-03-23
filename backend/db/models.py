"""
Typed async query helpers.

Every function accepts an aiosqlite.Connection and returns plain dicts or
lists of dicts.  No ORM — raw SQL so queries are transparent and Postgres
migration is straightforward (swap driver, adjust parameter placeholder).
"""

from __future__ import annotations

import json
from typing import Any

import aiosqlite

from .connection import new_id, ANON_USER_ID

Row = dict[str, Any]


# ── utility ────────────────────────────────────────────────────────────────────

def _row(row: aiosqlite.Row | None) -> Row | None:
    return dict(row) if row is not None else None


def _rows(rows) -> list[Row]:
    return [dict(r) for r in rows]


# ── sessions ───────────────────────────────────────────────────────────────────

async def create_session(conn: aiosqlite.Connection, user_id: str = ANON_USER_ID) -> str:
    sid = new_id()
    await conn.execute(
        "INSERT INTO sessions (id, user_id) VALUES (?, ?)",
        (sid, user_id),
    )
    await conn.commit()
    return sid


async def get_session(conn: aiosqlite.Connection, session_id: str) -> Row | None:
    async with conn.execute(
        "SELECT * FROM sessions WHERE id = ?", (session_id,)
    ) as cur:
        return _row(await cur.fetchone())


async def touch_session(conn: aiosqlite.Connection, session_id: str) -> None:
    await conn.execute(
        "UPDATE sessions SET last_seen = datetime('now') WHERE id = ?",
        (session_id,),
    )
    await conn.commit()


async def delete_session(conn: aiosqlite.Connection, session_id: str) -> None:
    await conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    await conn.commit()


# ── upload tokens ──────────────────────────────────────────────────────────────

async def create_upload_token(
    conn: aiosqlite.Connection, session_id: str, ttl_seconds: int = 300
) -> str:
    token = new_id()
    await conn.execute(
        """INSERT INTO upload_tokens (token, session_id, expires_at)
           VALUES (?, ?, datetime('now', ?))""",
        (token, session_id, f"+{ttl_seconds} seconds"),
    )
    await conn.commit()
    return token


async def consume_upload_token(
    conn: aiosqlite.Connection, token: str
) -> Row | None:
    """Validate and delete a token; return session row or None if invalid/expired."""
    async with conn.execute(
        """SELECT s.* FROM upload_tokens ut
           JOIN sessions s ON s.id = ut.session_id
           WHERE ut.token = ? AND ut.expires_at > datetime('now')""",
        (token,),
    ) as cur:
        session = _row(await cur.fetchone())
    if session:
        await conn.execute("DELETE FROM upload_tokens WHERE token = ?", (token,))
        await conn.commit()
    return session


# ── courses ────────────────────────────────────────────────────────────────────

async def create_course(
    conn: aiosqlite.Connection,
    creator_id: str,
    title: str,
    description: str | None = None,
    visibility: str = "draft",
) -> Row:
    cid = new_id()
    await conn.execute(
        "INSERT INTO courses (id, creator_id, title, description, visibility) VALUES (?, ?, ?, ?, ?)",
        (cid, creator_id, title, description, visibility),
    )
    await conn.commit()
    async with conn.execute("SELECT * FROM courses WHERE id = ?", (cid,)) as cur:
        return _row(await cur.fetchone())  # type: ignore[return-value]


async def get_course(conn: aiosqlite.Connection, course_id: str) -> Row | None:
    async with conn.execute(
        "SELECT * FROM courses WHERE id = ?", (course_id,)
    ) as cur:
        return _row(await cur.fetchone())


async def list_courses(
    conn: aiosqlite.Connection,
    user_id: str,
) -> list[Row]:
    """Return courses created by user_id or published to all users."""
    async with conn.execute(
        """SELECT * FROM courses
           WHERE creator_id = ? OR visibility = 'published'
           ORDER BY updated_at DESC""",
        (user_id,),
    ) as cur:
        return _rows(await cur.fetchall())


async def update_course(
    conn: aiosqlite.Connection, course_id: str, **kwargs: Any
) -> None:
    allowed = {"title", "description", "visibility"}
    for key, value in kwargs.items():
        if key not in allowed:
            continue
        await conn.execute(
            f"UPDATE courses SET {key} = ?, updated_at = datetime('now') WHERE id = ?",
            (value, course_id),
        )
    await conn.commit()


async def delete_course(conn: aiosqlite.Connection, course_id: str) -> None:
    await conn.execute("DELETE FROM courses WHERE id = ?", (course_id,))
    await conn.commit()


# ── lessons ────────────────────────────────────────────────────────────────────

async def create_lesson(
    conn: aiosqlite.Connection,
    creator_id: str,
    title: str,
    pdf_path: str | None = None,
    course_id: str | None = None,
    description: str | None = None,
    visibility: str = "draft",
) -> str:
    lid = new_id()
    await conn.execute(
        "INSERT INTO lessons (id, creator_id, title, pdf_path, course_id, description, visibility) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (lid, creator_id, title, pdf_path, course_id, description, visibility),
    )
    await conn.commit()
    return lid


async def get_lesson(conn: aiosqlite.Connection, lesson_id: str) -> Row | None:
    async with conn.execute(
        "SELECT * FROM lessons WHERE id = ?", (lesson_id,)
    ) as cur:
        return _row(await cur.fetchone())


async def list_lessons(
    conn: aiosqlite.Connection,
    user_id: str,
    limit: int = 50,
    offset: int = 0,
    course_id: str | None = None,
    standalone: bool = False,
) -> list[Row]:
    """
    Return lessons accessible to user_id (created by them or published),
    joined with their enrollment state (current_section_idx, completed).
    """
    sql = (
        "SELECT l.*, "
        "COALESCE(e.current_section_idx, 0) AS current_section_idx, "
        "COALESCE(e.completed, 0) AS completed, "
        "(SELECT COUNT(*) FROM lesson_sections WHERE lesson_id = l.id) AS section_count "
        "FROM lessons l "
        "LEFT JOIN lesson_enrollments e ON e.lesson_id = l.id AND e.user_id = ? "
        "WHERE (l.creator_id = ? OR l.visibility = 'published')"
    )
    params: list[Any] = [user_id, user_id]
    if standalone:
        sql += " AND l.course_id IS NULL"
    elif course_id is not None:
        sql += " AND l.course_id = ?"
        params.append(course_id)
    sql += " ORDER BY l.updated_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    async with conn.execute(sql, params) as cur:
        return _rows(await cur.fetchall())


_LESSON_UPDATE_SQL: dict[str, str] = {
    "title": "UPDATE lessons SET title = ?, updated_at = datetime('now') WHERE id = ?",
    "description": "UPDATE lessons SET description = ?, updated_at = datetime('now') WHERE id = ?",
    "course_id": "UPDATE lessons SET course_id = ?, updated_at = datetime('now') WHERE id = ?",
    "pdf_path": "UPDATE lessons SET pdf_path = ?, updated_at = datetime('now') WHERE id = ?",
    "visibility": "UPDATE lessons SET visibility = ?, updated_at = datetime('now') WHERE id = ?",
}


async def update_lesson(
    conn: aiosqlite.Connection, lesson_id: str, **kwargs: Any
) -> None:
    """Update lesson template columns.  Always bumps updated_at."""
    for key, value in kwargs.items():
        sql = _LESSON_UPDATE_SQL.get(key)
        if sql is None:
            continue
        await conn.execute(sql, (value, lesson_id))
    await conn.commit()


async def delete_lesson(conn: aiosqlite.Connection, lesson_id: str) -> None:
    await conn.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))
    await conn.commit()


# ── lesson enrollments ─────────────────────────────────────────────────────────

async def get_or_create_enrollment(
    conn: aiosqlite.Connection,
    lesson_id: str,
    user_id: str,
) -> Row:
    """Return the enrollment row, creating it lazily if it doesn't exist."""
    async with conn.execute(
        "SELECT * FROM lesson_enrollments WHERE lesson_id = ? AND user_id = ?",
        (lesson_id, user_id),
    ) as cur:
        row = _row(await cur.fetchone())
    if row is not None:
        return row
    eid = new_id()
    await conn.execute(
        """INSERT INTO lesson_enrollments (id, lesson_id, user_id)
           VALUES (?, ?, ?)""",
        (eid, lesson_id, user_id),
    )
    await conn.commit()
    async with conn.execute(
        "SELECT * FROM lesson_enrollments WHERE id = ?", (eid,)
    ) as cur:
        return _row(await cur.fetchone())  # type: ignore[return-value]


async def get_enrollment(
    conn: aiosqlite.Connection,
    lesson_id: str,
    user_id: str,
) -> Row | None:
    async with conn.execute(
        "SELECT * FROM lesson_enrollments WHERE lesson_id = ? AND user_id = ?",
        (lesson_id, user_id),
    ) as cur:
        return _row(await cur.fetchone())


async def get_enrollment_by_id(
    conn: aiosqlite.Connection,
    enrollment_id: str,
) -> Row | None:
    async with conn.execute(
        "SELECT * FROM lesson_enrollments WHERE id = ?", (enrollment_id,)
    ) as cur:
        return _row(await cur.fetchone())


_ENROLLMENT_UPDATE_SQL: dict[str, str] = {
    "current_section_idx": "UPDATE lesson_enrollments SET current_section_idx = ?, updated_at = datetime('now') WHERE id = ?",
    "completed": "UPDATE lesson_enrollments SET completed = ?, updated_at = datetime('now') WHERE id = ?",
    "lesson_goal": "UPDATE lesson_enrollments SET lesson_goal = ?, updated_at = datetime('now') WHERE id = ?",
}


async def update_enrollment(
    conn: aiosqlite.Connection, enrollment_id: str, **kwargs: Any
) -> None:
    """Update enrollment state columns.  Always bumps updated_at."""
    for key, value in kwargs.items():
        sql = _ENROLLMENT_UPDATE_SQL.get(key)
        if sql is None:
            continue
        await conn.execute(sql, (value, enrollment_id))
    await conn.commit()


# ── sections ───────────────────────────────────────────────────────────────────

async def upsert_sections(
    conn: aiosqlite.Connection,
    lesson_id: str,
    sections: list[dict],
) -> None:
    """Replace all sections for a lesson."""
    await conn.execute(
        "DELETE FROM lesson_sections WHERE lesson_id = ?", (lesson_id,)
    )
    for idx, sec in enumerate(sections):
        await conn.execute(
            """INSERT INTO lesson_sections
               (id, lesson_id, idx, title, content, key_concepts, page_start, page_end)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id(),
                lesson_id,
                idx,
                sec.get("title"),
                sec.get("content", ""),
                json.dumps(sec.get("key_concepts", [])),
                sec.get("page_start"),
                sec.get("page_end"),
            ),
        )
    await conn.commit()


async def get_sections(
    conn: aiosqlite.Connection, lesson_id: str
) -> list[Row]:
    async with conn.execute(
        "SELECT * FROM lesson_sections WHERE lesson_id = ? ORDER BY idx",
        (lesson_id,),
    ) as cur:
        rows = _rows(await cur.fetchall())
    # Deserialise key_concepts back to list
    for row in rows:
        row["key_concepts"] = json.loads(row.get("key_concepts", "[]"))
    return rows


# ── messages ───────────────────────────────────────────────────────────────────

async def upsert_messages(
    conn: aiosqlite.Connection,
    enrollment_id: str,
    messages: list[dict],
) -> None:
    """Replace all messages for an enrollment with a serialised list."""
    await conn.execute(
        "DELETE FROM messages WHERE enrollment_id = ?", (enrollment_id,)
    )
    for idx, msg in enumerate(messages):
        content = msg["content"]
        content_json = (
            json.dumps(content) if not isinstance(content, str) else content
        )
        await conn.execute(
            "INSERT INTO messages (id, enrollment_id, idx, role, content) VALUES (?, ?, ?, ?, ?)",
            (new_id(), enrollment_id, idx, msg["role"], content_json),
        )
    await conn.commit()


async def get_messages(
    conn: aiosqlite.Connection, enrollment_id: str
) -> list[dict]:
    """Return messages as Anthropic SDK-compatible dicts."""
    async with conn.execute(
        "SELECT role, content FROM messages WHERE enrollment_id = ? ORDER BY idx",
        (enrollment_id,),
    ) as cur:
        rows = await cur.fetchall()
    result = []
    for row in rows:
        role = row[0]
        content_raw = row[1]
        try:
            content = json.loads(content_raw)
        except (json.JSONDecodeError, TypeError):
            content = content_raw
        result.append({"role": role, "content": content})

    # Repair any dangling tool_use blocks (disconnect/save race condition).
    from .util import _strip_dangling_tool_use
    _strip_dangling_tool_use(result)

    return result


# ── enrollment assets ──────────────────────────────────────────────────────────

async def create_enrollment_asset(
    conn: aiosqlite.Connection,
    enrollment_id: str,
    section_idx: int,
    image_path: str,
    prompt: str,
    tool_use_id: str,
    revised_prompt: str = "",
    idx: int = 0,
    asset_type: str = "ai_image",
) -> Row:
    aid = new_id()
    await conn.execute(
        """INSERT INTO enrollment_assets
           (id, enrollment_id, section_idx, asset_type, image_path, prompt,
            revised_prompt, tool_use_id, idx)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (aid, enrollment_id, section_idx, asset_type, image_path, prompt,
         revised_prompt, tool_use_id, idx),
    )
    await conn.commit()
    async with conn.execute(
        "SELECT * FROM enrollment_assets WHERE id = ?", (aid,)
    ) as cur:
        return _row(await cur.fetchone())  # type: ignore[return-value]


async def get_enrollment_assets(
    conn: aiosqlite.Connection,
    enrollment_id: str,
    section_idx: int | None = None,
) -> list[Row]:
    if section_idx is not None:
        async with conn.execute(
            """SELECT * FROM enrollment_assets
               WHERE enrollment_id = ? AND section_idx = ?
               ORDER BY section_idx, idx""",
            (enrollment_id, section_idx),
        ) as cur:
            return _rows(await cur.fetchall())
    async with conn.execute(
        """SELECT * FROM enrollment_assets
           WHERE enrollment_id = ?
           ORDER BY section_idx, idx""",
        (enrollment_id,),
    ) as cur:
        return _rows(await cur.fetchall())


# ── users / auth ───────────────────────────────────────────────────────────────

async def create_user(
    conn: aiosqlite.Connection, email: str, password_hash: str, username: str = ""
) -> Row:
    uid = new_id()
    clean_email = email.lower().strip()
    uname = username.strip() if username.strip() else clean_email.split("@")[0][:30]
    await conn.execute(
        "INSERT INTO users (id, email, password_hash, email_verified, username) VALUES (?, ?, ?, 0, ?)",
        (uid, clean_email, password_hash, uname),
    )
    await conn.commit()
    async with conn.execute("SELECT * FROM users WHERE id = ?", (uid,)) as cur:
        return _row(await cur.fetchone())  # type: ignore[return-value]


async def get_user_by_email(
    conn: aiosqlite.Connection, email: str
) -> Row | None:
    async with conn.execute(
        "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
    ) as cur:
        return _row(await cur.fetchone())


async def get_user_by_id(
    conn: aiosqlite.Connection, user_id: str
) -> Row | None:
    async with conn.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ) as cur:
        return _row(await cur.fetchone())


async def delete_user(conn: aiosqlite.Connection, user_id: str) -> None:
    await conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    await conn.commit()


async def mark_email_verified(
    conn: aiosqlite.Connection, user_id: str
) -> None:
    await conn.execute(
        "UPDATE users SET email_verified = 1 WHERE id = ?", (user_id,)
    )
    await conn.commit()


async def create_verification_token(
    conn: aiosqlite.Connection,
    user_id: str,
    token: str,
    ttl_hours: int = 24,
) -> None:
    # Remove any existing tokens for this user first.
    await conn.execute(
        "DELETE FROM email_verifications WHERE user_id = ?", (user_id,)
    )
    await conn.execute(
        """INSERT INTO email_verifications (token, user_id, expires_at)
           VALUES (?, ?, datetime('now', ?))""",
        (token, user_id, f"+{ttl_hours} hours"),
    )
    await conn.commit()


async def consume_verification_token(
    conn: aiosqlite.Connection, token: str
) -> str | None:
    """Validate and delete token; returns user_id or None if invalid/expired."""
    async with conn.execute(
        """SELECT user_id FROM email_verifications
           WHERE token = ? AND expires_at > datetime('now')""",
        (token,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    user_id = row[0]
    await conn.execute(
        "DELETE FROM email_verifications WHERE token = ?", (token,)
    )
    await conn.commit()
    return user_id


async def create_password_reset_token(
    conn: aiosqlite.Connection,
    user_id: str,
    token: str,
    ttl_hours: int = 1,
) -> None:
    await conn.execute(
        "DELETE FROM password_reset_tokens WHERE user_id = ?", (user_id,)
    )
    await conn.execute(
        """INSERT INTO password_reset_tokens (token, user_id, expires_at)
           VALUES (?, ?, datetime('now', ?))""",
        (token, user_id, f"+{ttl_hours} hours"),
    )
    await conn.commit()


async def consume_password_reset_token(
    conn: aiosqlite.Connection, token: str
) -> str | None:
    """Validate and delete token; returns user_id or None if invalid/expired."""
    async with conn.execute(
        """SELECT user_id FROM password_reset_tokens
           WHERE token = ? AND expires_at > datetime('now')""",
        (token,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    user_id = row[0]
    await conn.execute(
        "DELETE FROM password_reset_tokens WHERE token = ?", (token,)
    )
    await conn.commit()
    return user_id


async def update_password_hash(
    conn: aiosqlite.Connection, user_id: str, password_hash: str
) -> None:
    await conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id)
    )
    await conn.commit()


# ── personas ───────────────────────────────────────────────────────────────────

BUILT_IN_PERSONAS = [
    {
        "id": "default",
        "name": "Default",
        "instructions": (
            "You are a helpful instructor."
            "Guide the student through the course material,"
            " answer their questions,"
            " and encourage them to think critically."
            "Always be patient and supportive."),
        "user_id": None,
    },
    {
        "id": "socratic",
        "name": "Socratic",
        "instructions": (
            "Guide the student by asking probing questions rather than stating facts directly. "
            "Never give the answer outright; instead, lead with 'What do you think...?' or "
            "'How might that connect to...?'. Celebrate partial answers and build on them."
        ),
        "user_id": None,
    },
    {
        "id": "encouraging",
        "name": "Encouraging Coach",
        "instructions": (
            "Be warm, enthusiastic, and patient. Celebrate every correct answer. "
            "When the student struggles, reframe the challenge positively and offer hints "
            "before explanations. Use phrases like 'Great effort!' and 'You're almost there!'."
        ),
        "user_id": None,
    },
]


async def seed_admin_users(
    conn: aiosqlite.Connection,
    admin_emails: tuple[str, ...] | list[str] | set[str] = (),
) -> None:
    """Grant admin to listed emails that already exist (grant-only)."""
    normalized = {
        str(email).strip().lower()
        for email in admin_emails
        if str(email).strip()
    }
    for email in normalized:
        await conn.execute(
            "UPDATE users SET is_admin = 1 WHERE email = ?",
            (email,),
        )
    await conn.commit()


async def get_user_is_admin(conn: aiosqlite.Connection, user_id: str) -> bool:
    async with conn.execute(
        "SELECT is_admin FROM users WHERE id = ?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    return bool(row and row[0])


async def list_users_for_admin_iam(conn: aiosqlite.Connection) -> list[Row]:
    async with conn.execute(
        """SELECT id, email, display_name, username, email_verified, is_admin, created_at
           FROM users
           ORDER BY created_at"""
    ) as cur:
        return _rows(await cur.fetchall())


async def count_admin_users(conn: aiosqlite.Connection) -> int:
    async with conn.execute(
        "SELECT COUNT(*) FROM users WHERE is_admin = 1"
    ) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def set_user_admin(
    conn: aiosqlite.Connection,
    user_id: str,
    *,
    is_admin: bool,
) -> None:
    await conn.execute(
        "UPDATE users SET is_admin = ? WHERE id = ?",
        (1 if is_admin else 0, user_id),
    )
    await conn.commit()


async def seed_personas(conn: aiosqlite.Connection) -> None:
    for p in BUILT_IN_PERSONAS:
        await conn.execute(
            """INSERT OR IGNORE INTO personas (id, user_id, name, instructions)
               VALUES (?, ?, ?, ?)""",
            (p["id"], p["user_id"], p["name"], p["instructions"]),
        )
    await conn.commit()


async def get_personas(
    conn: aiosqlite.Connection, user_id: str | None = None
) -> list[Row]:
    """Return built-in personas plus any owned by user_id."""
    if user_id:
        async with conn.execute(
            "SELECT * FROM personas WHERE user_id IS NULL OR user_id = ? ORDER BY created_at",
            (user_id,),
        ) as cur:
            return _rows(await cur.fetchall())
    async with conn.execute(
        "SELECT * FROM personas WHERE user_id IS NULL ORDER BY created_at"
    ) as cur:
        return _rows(await cur.fetchall())


async def create_persona(
    conn: aiosqlite.Connection,
    persona_id: str,
    user_id: str,
    name: str,
    instructions: str,
) -> Row:
    await conn.execute(
        "INSERT INTO personas (id, user_id, name, instructions) VALUES (?, ?, ?, ?)",
        (persona_id, user_id, name, instructions),
    )
    await conn.commit()
    async with conn.execute(
        "SELECT * FROM personas WHERE id = ?", (persona_id,)
    ) as cur:
        return _row(await cur.fetchone())  # type: ignore[return-value]


async def delete_persona(
    conn: aiosqlite.Connection, persona_id: str, user_id: str
) -> bool:
    """Delete only if owned by user_id.  Returns True if deleted."""
    async with conn.execute(
        "DELETE FROM personas WHERE id = ? AND user_id = ?",
        (persona_id, user_id),
    ) as cur:
        deleted = cur.rowcount > 0
    if deleted:
        await conn.commit()
    return deleted


# ── points / gamification ───────────────────────────────────────────────────────

async def get_enrollment_section_idx(
    conn: aiosqlite.Connection, enrollment_id: str
) -> int:
    """Return the persisted current_section_idx for an enrollment (0 if not found)."""
    async with conn.execute(
        "SELECT current_section_idx FROM lesson_enrollments WHERE id = ?", (enrollment_id,)
    ) as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def upsert_user_points(
    conn: aiosqlite.Connection,
    user_id: str,
    *,
    delta_points: int = 0,
    delta_lessons: int = 0,
    delta_sections: int = 0,
    streak: int | None = None,
    longest_streak: int | None = None,
    last_lesson_date: str | None = None,
) -> None:
    """Create or update the user_points row atomically."""
    await conn.execute(
        """INSERT INTO user_points (user_id, total_points, lessons_completed, sections_advanced,
               current_streak, longest_streak, last_lesson_date, updated_at)
           VALUES (?, ?, ?, ?, COALESCE(?, 0), COALESCE(?, 0), ?, datetime('now'))
           ON CONFLICT(user_id) DO UPDATE SET
               total_points      = total_points + excluded.total_points,
               lessons_completed = lessons_completed + excluded.lessons_completed,
               sections_advanced = sections_advanced + excluded.sections_advanced,
               current_streak    = CASE WHEN excluded.current_streak != 0 OR ? IS NOT NULL
                                        THEN excluded.current_streak
                                        ELSE current_streak END,
               longest_streak    = CASE WHEN excluded.longest_streak != 0 OR ? IS NOT NULL
                                        THEN MAX(longest_streak, excluded.longest_streak)
                                        ELSE longest_streak END,
               last_lesson_date  = CASE WHEN excluded.last_lesson_date IS NOT NULL
                                        THEN excluded.last_lesson_date
                                        ELSE last_lesson_date END,
               updated_at        = datetime('now')""",
        (
            user_id,
            delta_points,
            delta_lessons,
            delta_sections,
            streak,
            longest_streak,
            last_lesson_date,
            streak,   # for streak CASE
            longest_streak,  # for longest_streak CASE
        ),
    )
    await conn.commit()


async def get_user_points(
    conn: aiosqlite.Connection, user_id: str
) -> Row | None:
    async with conn.execute(
        "SELECT * FROM user_points WHERE user_id = ?", (user_id,)
    ) as cur:
        return _row(await cur.fetchone())


async def insert_point_event(
    conn: aiosqlite.Connection,
    event_id: str,
    user_id: str,
    event_type: str,
    points: int,
    enrollment_id: str | None = None,
) -> bool:
    """Insert a point event. Returns False if a unique-constrained duplicate was ignored."""
    async with conn.execute(
        """INSERT OR IGNORE INTO point_events (id, user_id, enrollment_id, event_type, points)
           VALUES (?, ?, ?, ?, ?)""",
        (event_id, user_id, enrollment_id, event_type, points),
    ) as cur:
        inserted = cur.rowcount > 0
    if inserted:
        await conn.commit()
    return inserted


async def get_leaderboard(
    conn: aiosqlite.Connection, limit: int = 50
) -> list[Row]:
    """Return top users by total_points, excluding the anonymous user."""
    from .connection import ANON_USER_ID
    async with conn.execute(
        """SELECT u.username, u.display_name,
                  up.total_points, up.lessons_completed, up.sections_advanced,
                  up.current_streak, up.longest_streak,
                  ROW_NUMBER() OVER (ORDER BY up.total_points DESC) AS rank
           FROM user_points up
           JOIN users u ON u.id = up.user_id
           WHERE up.total_points > 0 AND up.user_id != ?
           ORDER BY up.total_points DESC
           LIMIT ?""",
        (ANON_USER_ID, limit),
    ) as cur:
        return _rows(await cur.fetchall())


async def get_user_rank(
    conn: aiosqlite.Connection, user_id: str
) -> int | None:
    """Return the rank of a specific user (1-based), or None if they have no points."""
    from .connection import ANON_USER_ID
    async with conn.execute(
        """SELECT COUNT(*) + 1
           FROM user_points
           WHERE total_points > (
               SELECT COALESCE(total_points, 0) FROM user_points WHERE user_id = ?
           )
           AND user_id != ?""",
        (user_id, ANON_USER_ID),
    ) as cur:
        row = await cur.fetchone()
    # Confirm user actually has a points row
    pts = await get_user_points(conn, user_id)
    if pts is None:
        return None
    return int(row[0]) if row else None
    return deleted
