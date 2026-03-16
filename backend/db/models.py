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
    user_id: str,
    title: str,
    description: str | None = None,
) -> Row:
    cid = new_id()
    await conn.execute(
        "INSERT INTO courses (id, user_id, title, description) VALUES (?, ?, ?, ?)",
        (cid, user_id, title, description),
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
    async with conn.execute(
        "SELECT * FROM courses WHERE user_id = ? ORDER BY updated_at DESC",
        (user_id,),
    ) as cur:
        return _rows(await cur.fetchall())


async def update_course(
    conn: aiosqlite.Connection, course_id: str, **kwargs: Any
) -> None:
    allowed = {"title", "description"}
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
    user_id: str,
    title: str,
    pdf_path: str | None = None,
    course_id: str | None = None,
    description: str | None = None,
) -> str:
    lid = new_id()
    await conn.execute(
        "INSERT INTO lessons (id, user_id, title, pdf_path, course_id, description) VALUES (?, ?, ?, ?, ?, ?)",
        (lid, user_id, title, pdf_path, course_id, description),
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
    sql = (
        "SELECT l.*, "
        "(SELECT COUNT(*) FROM lesson_sections WHERE lesson_id = l.id) AS section_count "
        "FROM lessons l WHERE l.user_id = ?"
    )
    params: list[Any] = [user_id]
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
    "current_section_idx": "UPDATE lessons SET current_section_idx = ?, updated_at = datetime('now') WHERE id = ?",
    "completed": "UPDATE lessons SET completed = ?, updated_at = datetime('now') WHERE id = ?",
    "lesson_goal": "UPDATE lessons SET lesson_goal = ?, updated_at = datetime('now') WHERE id = ?",
}


async def update_lesson(
    conn: aiosqlite.Connection, lesson_id: str, **kwargs: Any
) -> None:
    """Update lesson columns.  Always bumps updated_at."""
    for key, value in kwargs.items():
        sql = _LESSON_UPDATE_SQL.get(key)
        if sql is None:
            continue
        await conn.execute(sql, (value, lesson_id))
    await conn.commit()


async def delete_lesson(conn: aiosqlite.Connection, lesson_id: str) -> None:
    await conn.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))
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
    lesson_id: str,
    messages: list[dict],
) -> None:
    """Replace all messages for a lesson with a serialised list."""
    await conn.execute(
        "DELETE FROM messages WHERE lesson_id = ?", (lesson_id,)
    )
    for idx, msg in enumerate(messages):
        content = msg["content"]
        content_json = (
            json.dumps(content) if not isinstance(content, str) else content
        )
        await conn.execute(
            "INSERT INTO messages (id, lesson_id, idx, role, content) VALUES (?, ?, ?, ?, ?)",
            (new_id(), lesson_id, idx, msg["role"], content_json),
        )
    await conn.commit()


async def get_messages(
    conn: aiosqlite.Connection, lesson_id: str
) -> list[dict]:
    """Return messages as Anthropic SDK-compatible dicts."""
    async with conn.execute(
        "SELECT role, content FROM messages WHERE lesson_id = ? ORDER BY idx",
        (lesson_id,),
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
    from shared.teaching_agent import _strip_dangling_tool_use
    _strip_dangling_tool_use(result)

    return result


# ── users / auth ───────────────────────────────────────────────────────────────

async def create_user(
    conn: aiosqlite.Connection, email: str, password_hash: str
) -> Row:
    uid = new_id()
    await conn.execute(
        "INSERT INTO users (id, email, password_hash, email_verified) VALUES (?, ?, ?, 0)",
        (uid, email.lower().strip(), password_hash),
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
        "instructions": "",
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


ADMIN_EMAILS: frozenset[str] = frozenset({"rcdoug03@louisville.edu"})


async def seed_admin_users(conn: aiosqlite.Connection) -> None:
    """Ensure known admin emails have is_admin=1 if their account exists."""
    for email in ADMIN_EMAILS:
        await conn.execute(
            "UPDATE users SET is_admin = 1 WHERE email = ?", (email,)
        )
    await conn.commit()


async def get_user_is_admin(conn: aiosqlite.Connection, user_id: str) -> bool:
    async with conn.execute(
        "SELECT is_admin FROM users WHERE id = ?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    return bool(row and row[0])


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
