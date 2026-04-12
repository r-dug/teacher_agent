"""
Typed async query helpers.

Every function accepts an asyncpg.Connection and returns plain dicts or
lists of dicts.  No ORM — raw SQL so queries are transparent.

Parameter style: $1, $2, ... (PostgreSQL / asyncpg).
Multi-statement write functions are wrapped in explicit transactions.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from .connection import new_id, ANON_USER_ID

Row = dict[str, Any]


# ── utility ────────────────────────────────────────────────────────────────────

def _row(row: asyncpg.Record | None) -> Row | None:
    return dict(row) if row is not None else None


def _rows(rows: list[asyncpg.Record]) -> list[Row]:
    return [dict(r) for r in rows]


# ── sessions ───────────────────────────────────────────────────────────────────

async def create_session(conn: asyncpg.Connection, user_id: str = ANON_USER_ID) -> str:
    sid = new_id()
    await conn.execute(
        "INSERT INTO sessions (id, user_id) VALUES ($1, $2)",
        sid, user_id,
    )
    return sid


async def get_session(conn: asyncpg.Connection, session_id: str) -> Row | None:
    return _row(await conn.fetchrow(
        "SELECT * FROM sessions WHERE id = $1", session_id
    ))


async def touch_session(conn: asyncpg.Connection, session_id: str) -> None:
    await conn.execute(
        "UPDATE sessions SET last_seen = NOW() WHERE id = $1",
        session_id,
    )


async def delete_session(conn: asyncpg.Connection, session_id: str) -> None:
    await conn.execute("DELETE FROM sessions WHERE id = $1", session_id)


async def expire_old_sessions(conn: asyncpg.Connection, retention_days: int) -> int:
    """Delete sessions not seen in the last *retention_days* days. Returns row count."""
    result = await conn.execute(
        "DELETE FROM sessions WHERE last_seen < NOW() - ($1 || ' days')::INTERVAL",
        str(retention_days),
    )
    # asyncpg returns e.g. "DELETE 3"
    return int(result.split()[-1])


# ── upload tokens ──────────────────────────────────────────────────────────────

async def create_upload_token(
    conn: asyncpg.Connection, session_id: str, ttl_seconds: int = 300
) -> str:
    token = new_id()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    await conn.execute(
        "INSERT INTO upload_tokens (token, session_id, expires_at) VALUES ($1, $2, $3)",
        token, session_id, expires_at,
    )
    return token


async def consume_upload_token(
    conn: asyncpg.Connection, token: str
) -> Row | None:
    """Validate and delete a token; return session row or None if invalid/expired."""
    async with conn.transaction():
        session = _row(await conn.fetchrow(
            """SELECT s.* FROM upload_tokens ut
               JOIN sessions s ON s.id = ut.session_id
               WHERE ut.token = $1 AND ut.expires_at > CLOCK_TIMESTAMP()""",
            token,
        ))
        if session:
            await conn.execute("DELETE FROM upload_tokens WHERE token = $1", token)
    return session


# ── courses ────────────────────────────────────────────────────────────────────

async def create_course(
    conn: asyncpg.Connection,
    creator_id: str,
    title: str,
    description: str | None = None,
    visibility: str = "draft",
) -> Row:
    cid = new_id()
    await conn.execute(
        "INSERT INTO courses (id, creator_id, title, description, visibility) VALUES ($1, $2, $3, $4, $5)",
        cid, creator_id, title, description, visibility,
    )
    return _row(await conn.fetchrow("SELECT * FROM courses WHERE id = $1", cid))  # type: ignore[return-value]


async def get_course(conn: asyncpg.Connection, course_id: str) -> Row | None:
    return _row(await conn.fetchrow("SELECT * FROM courses WHERE id = $1", course_id))


async def list_courses(
    conn: asyncpg.Connection,
    user_id: str,
) -> list[Row]:
    return _rows(await conn.fetch(
        """SELECT * FROM courses
           WHERE creator_id = $1 OR visibility = 'published'
           ORDER BY updated_at DESC""",
        user_id,
    ))


async def update_course(
    conn: asyncpg.Connection, course_id: str, **kwargs: Any
) -> None:
    allowed = {"title", "description", "visibility", "visual_aid_config", "default_persona_id"}
    async with conn.transaction():
        for key, value in kwargs.items():
            if key not in allowed:
                continue
            await conn.execute(
                f"UPDATE courses SET {key} = $1, updated_at = NOW() WHERE id = $2",
                value, course_id,
            )


async def delete_course(conn: asyncpg.Connection, course_id: str) -> None:
    await conn.execute("DELETE FROM courses WHERE id = $1", course_id)


# ── lessons ────────────────────────────────────────────────────────────────────

async def create_lesson(
    conn: asyncpg.Connection,
    creator_id: str,
    title: str,
    pdf_path: str | None = None,
    course_id: str | None = None,
    description: str | None = None,
    visibility: str = "draft",
) -> str:
    lid = new_id()
    await conn.execute(
        "INSERT INTO lessons (id, creator_id, title, pdf_path, course_id, description, visibility) VALUES ($1, $2, $3, $4, $5, $6, $7)",
        lid, creator_id, title, pdf_path, course_id, description, visibility,
    )
    return lid


async def get_lesson(conn: asyncpg.Connection, lesson_id: str) -> Row | None:
    return _row(await conn.fetchrow("SELECT * FROM lessons WHERE id = $1", lesson_id))


async def list_lessons(
    conn: asyncpg.Connection,
    user_id: str,
    limit: int = 50,
    offset: int = 0,
    course_id: str | None = None,
    standalone: bool = False,
) -> list[Row]:
    extra_joins = ""
    order_clause = "l.updated_at DESC"
    params: list[Any] = [user_id]
    where_extra = ""
    if standalone:
        where_extra = " AND l.course_id IS NULL"
    elif course_id is not None:
        params.append(course_id)
        where_extra = f" AND l.course_id = ${len(params)}"
        extra_joins = (
            " LEFT JOIN course_chapter_lessons ccl ON ccl.lesson_id = l.id AND ccl.course_id = l.course_id"
            " LEFT JOIN course_chapter_drafts ccd ON ccd.id = ccl.chapter_id"
        )
        order_clause = "ccd.idx ASC NULLS LAST, l.updated_at DESC"
    sql = (
        "SELECT l.*, "
        "COALESCE(e.current_section_idx, 0) AS current_section_idx, "
        "COALESCE(e.completed, 0) AS completed, "
        "(SELECT COUNT(*) FROM lesson_sections WHERE lesson_id = l.id) AS section_count "
        "FROM lessons l "
        "LEFT JOIN lesson_enrollments e ON e.lesson_id = l.id AND e.user_id = $1"
        f"{extra_joins}"
        f" WHERE (l.creator_id = $1 OR l.visibility = 'published'){where_extra}"
    )
    params.extend([limit, offset])
    sql += f" ORDER BY {order_clause} LIMIT ${len(params) - 1} OFFSET ${len(params)}"
    return _rows(await conn.fetch(sql, *params))


_LESSON_UPDATE_SQL: dict[str, str] = {
    "title":             "UPDATE lessons SET title = $1,             updated_at = NOW() WHERE id = $2",
    "description":       "UPDATE lessons SET description = $1,       updated_at = NOW() WHERE id = $2",
    "course_id":         "UPDATE lessons SET course_id = $1,         updated_at = NOW() WHERE id = $2",
    "pdf_path":          "UPDATE lessons SET pdf_path = $1,          updated_at = NOW() WHERE id = $2",
    "visibility":        "UPDATE lessons SET visibility = $1,        updated_at = NOW() WHERE id = $2",
    "visual_aid_config": "UPDATE lessons SET visual_aid_config = $1, updated_at = NOW() WHERE id = $2",
}


async def update_lesson(
    conn: asyncpg.Connection, lesson_id: str, **kwargs: Any
) -> None:
    async with conn.transaction():
        for key, value in kwargs.items():
            sql = _LESSON_UPDATE_SQL.get(key)
            if sql is None:
                continue
            await conn.execute(sql, value, lesson_id)


async def delete_lesson(conn: asyncpg.Connection, lesson_id: str) -> None:
    await conn.execute("DELETE FROM lessons WHERE id = $1", lesson_id)


# ── lesson enrollments ─────────────────────────────────────────────────────────

async def get_or_create_enrollment(
    conn: asyncpg.Connection,
    lesson_id: str,
    user_id: str,
) -> Row:
    row = _row(await conn.fetchrow(
        "SELECT * FROM lesson_enrollments WHERE lesson_id = $1 AND user_id = $2",
        lesson_id, user_id,
    ))
    if row is not None:
        return row
    eid = new_id()
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO lesson_enrollments (id, lesson_id, user_id) VALUES ($1, $2, $3)",
            eid, lesson_id, user_id,
        )
    return _row(await conn.fetchrow(  # type: ignore[return-value]
        "SELECT * FROM lesson_enrollments WHERE id = $1", eid
    ))


async def get_enrollment(
    conn: asyncpg.Connection,
    lesson_id: str,
    user_id: str,
) -> Row | None:
    return _row(await conn.fetchrow(
        "SELECT * FROM lesson_enrollments WHERE lesson_id = $1 AND user_id = $2",
        lesson_id, user_id,
    ))


async def get_enrollment_by_id(
    conn: asyncpg.Connection,
    enrollment_id: str,
) -> Row | None:
    return _row(await conn.fetchrow(
        "SELECT * FROM lesson_enrollments WHERE id = $1", enrollment_id
    ))


_ENROLLMENT_ALLOWED_COLS = {"current_section_idx", "completed", "lesson_goal", "task_progress", "persona_id"}


def parse_task_progress(raw: str | None) -> dict[str, list[dict]]:
    """Deserialize task_progress JSON from DB."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


_DEFAULT_VISUAL_AID_CONFIG: dict = {
    "pdf_pages": True,
    "generated_images": {"enabled": False, "persistence": "persistent", "timing": "spontaneous"},
    "web_images": {"enabled": False, "persistence": "ephemeral", "timing": "spontaneous"},
}


def parse_visual_aid_config(raw: str | None) -> dict:
    """Deserialize visual_aid_config JSON, filling in defaults."""
    if not raw or raw == "{}":
        return dict(_DEFAULT_VISUAL_AID_CONFIG)
    try:
        cfg = json.loads(raw)
        # Merge with defaults so missing keys get sensible values
        result = dict(_DEFAULT_VISUAL_AID_CONFIG)
        if "pdf_pages" in cfg:
            result["pdf_pages"] = cfg["pdf_pages"]
        for key in ("generated_images", "web_images"):
            if key in cfg:
                result[key] = {**_DEFAULT_VISUAL_AID_CONFIG[key], **cfg[key]}
        return result
    except (json.JSONDecodeError, TypeError):
        return dict(_DEFAULT_VISUAL_AID_CONFIG)


async def update_enrollment(
    conn: asyncpg.Connection, enrollment_id: str, **kwargs: Any
) -> None:
    cols = {k: v for k, v in kwargs.items() if k in _ENROLLMENT_ALLOWED_COLS}
    if not cols:
        return
    assignments = ", ".join(f"{k} = ${i+1}" for i, k in enumerate(cols))
    values = list(cols.values()) + [enrollment_id]
    await conn.execute(
        f"UPDATE lesson_enrollments SET {assignments}, updated_at = NOW() WHERE id = ${len(values)}",
        *values,
    )


async def reset_enrollment(conn: asyncpg.Connection, enrollment_id: str) -> None:
    """Reset an enrollment to its initial state (retake)."""
    async with conn.transaction():
        await conn.execute(
            "UPDATE lesson_enrollments "
            "SET current_section_idx = 0, completed = 0, lesson_goal = NULL, "
            "    task_progress = '{}', updated_at = NOW() "
            "WHERE id = $1",
            enrollment_id,
        )
        await conn.execute(
            "DELETE FROM messages WHERE enrollment_id = $1", enrollment_id
        )


# ── sections ───────────────────────────────────────────────────────────────────

async def upsert_sections(
    conn: asyncpg.Connection,
    lesson_id: str,
    sections: list[dict],
) -> None:
    async with conn.transaction():
        await conn.execute(
            "DELETE FROM lesson_sections WHERE lesson_id = $1", lesson_id
        )
        for idx, sec in enumerate(sections):
            await conn.execute(
                """INSERT INTO lesson_sections
                   (id, lesson_id, idx, title, content, key_concepts, page_start, page_end)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                new_id(),
                lesson_id,
                idx,
                sec.get("title"),
                sec.get("content", ""),
                json.dumps(sec.get("key_concepts", [])),
                sec.get("page_start"),
                sec.get("page_end"),
            )
        # Auto-populate PDF page assets for each section's page range.
        await populate_pdf_page_assets(conn, lesson_id)


async def get_sections(
    conn: asyncpg.Connection, lesson_id: str
) -> list[Row]:
    rows = _rows(await conn.fetch(
        "SELECT * FROM lesson_sections WHERE lesson_id = $1 ORDER BY idx",
        lesson_id,
    ))
    for row in rows:
        row["key_concepts"] = json.loads(row.get("key_concepts", "[]"))
    return rows


async def search_sections(
    conn: asyncpg.Connection,
    lesson_id: str,
    query: str,
    exclude_idx: int | None = None,
    limit: int = 3,
) -> list[dict]:
    """Full-text search over lesson_sections.content for RAG retrieval."""
    sql = """
        SELECT idx, title, content,
               ts_rank(to_tsvector('english', content), plainto_tsquery('english', $2)) AS rank
        FROM lesson_sections
        WHERE lesson_id = $1
          AND to_tsvector('english', content) @@ plainto_tsquery('english', $2)
    """
    params: list = [lesson_id, query]
    if exclude_idx is not None:
        sql += " AND idx != $3"
        params.append(exclude_idx)
    sql += " ORDER BY rank DESC LIMIT $" + str(len(params) + 1)
    params.append(limit)

    rows = await conn.fetch(sql, *params)
    results = []
    for r in rows:
        content = r["content"] or ""
        # Truncate to ~500 chars for concise tool results
        excerpt = content[:500] + ("..." if len(content) > 500 else "")
        results.append({
            "idx": r["idx"],
            "title": r["title"] or f"Section {r['idx'] + 1}",
            "excerpt": excerpt,
        })
    return results


# ── section assets ─────────────────────────────────────────────────────────────

async def create_section_asset(
    conn: asyncpg.Connection,
    section_id: str,
    asset_type: str,
    idx: int = 0,
    page_start: int | None = None,
    page_end: int | None = None,
    image_path: str | None = None,
    caption: str | None = None,
) -> Row:
    aid = new_id()
    await conn.execute(
        """INSERT INTO section_assets
           (id, section_id, asset_type, idx, page_start, page_end, image_path, caption)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
           ON CONFLICT (section_id, idx) DO UPDATE SET
             asset_type = excluded.asset_type,
             page_start = excluded.page_start,
             page_end   = excluded.page_end,
             image_path = excluded.image_path,
             caption    = excluded.caption""",
        aid, section_id, asset_type, idx, page_start, page_end, image_path, caption,
    )
    return _row(await conn.fetchrow(  # type: ignore[return-value]
        "SELECT * FROM section_assets WHERE section_id = $1 AND idx = $2",
        section_id, idx,
    ))


async def get_section_assets_by_lesson(
    conn: asyncpg.Connection,
    lesson_id: str,
) -> dict[str, list[Row]]:
    """Return section assets grouped by section_id for all sections of a lesson."""
    rows = _rows(await conn.fetch(
        """SELECT sa.* FROM section_assets sa
           JOIN lesson_sections ls ON ls.id = sa.section_id
           WHERE ls.lesson_id = $1
           ORDER BY ls.idx, sa.idx""",
        lesson_id,
    ))
    grouped: dict[str, list[Row]] = {}
    for r in rows:
        grouped.setdefault(r["section_id"], []).append(r)
    return grouped


async def populate_pdf_page_assets(
    conn: asyncpg.Connection,
    lesson_id: str,
) -> int:
    """Auto-create section_assets of type 'pdf_pages' from each section's page range.

    One asset per page in the section's [page_start, page_end] range.
    Returns the number of assets created.
    """
    sections = await get_sections(conn, lesson_id)
    count = 0
    for sec in sections:
        page_start = sec.get("page_start")
        page_end = sec.get("page_end") or page_start
        if page_start is None:
            continue
        for i, page in enumerate(range(page_start, page_end + 1)):
            await create_section_asset(
                conn,
                section_id=sec["id"],
                asset_type="pdf_pages",
                idx=i,
                page_start=page,
                page_end=page,
                caption=f"Page {page}",
            )
            count += 1
    return count


# ── messages ───────────────────────────────────────────────────────────────────

async def upsert_messages(
    conn: asyncpg.Connection,
    enrollment_id: str,
    messages: list[dict],
) -> None:
    records = []
    for idx, msg in enumerate(messages):
        content = msg["content"]
        content_json = (
            json.dumps(content) if not isinstance(content, str) else content
        )
        records.append((new_id(), enrollment_id, idx, msg["role"], content_json))

    async with conn.transaction():
        await conn.execute(
            "DELETE FROM messages WHERE enrollment_id = $1", enrollment_id
        )
        await conn.executemany(
            "INSERT INTO messages (id, enrollment_id, idx, role, content) VALUES ($1, $2, $3, $4, $5)",
            records,
        )


async def append_message(
    conn: asyncpg.Connection,
    enrollment_id: str,
    role: str,
    content,
) -> None:
    """Append a single message to the conversation. Assigns the next idx."""
    content_json = json.dumps(content) if not isinstance(content, str) else content
    next_idx = await conn.fetchval(
        "SELECT COALESCE(MAX(idx), -1) + 1 FROM messages WHERE enrollment_id = $1",
        enrollment_id,
    )
    await conn.execute(
        "INSERT INTO messages (id, enrollment_id, idx, role, content) VALUES ($1, $2, $3, $4, $5)",
        new_id(), enrollment_id, next_idx, role, content_json,
    )


async def get_messages(
    conn: asyncpg.Connection, enrollment_id: str
) -> list[dict]:
    rows = await conn.fetch(
        "SELECT role, content FROM messages WHERE enrollment_id = $1 ORDER BY idx",
        enrollment_id,
    )
    result = []
    for row in rows:
        role = row[0]
        content_raw = row[1]
        try:
            content = json.loads(content_raw)
        except (json.JSONDecodeError, TypeError):
            content = content_raw
        result.append({"role": role, "content": content})

    from .util import _strip_dangling_tool_use
    _strip_dangling_tool_use(result)

    return result


# ── enrollment assets ──────────────────────────────────────────────────────────

async def create_enrollment_asset(
    conn: asyncpg.Connection,
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
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
        aid, enrollment_id, section_idx, asset_type, image_path, prompt,
        revised_prompt, tool_use_id, idx,
    )
    return _row(await conn.fetchrow(  # type: ignore[return-value]
        "SELECT * FROM enrollment_assets WHERE id = $1", aid
    ))


async def get_enrollment_assets(
    conn: asyncpg.Connection,
    enrollment_id: str,
    section_idx: int | None = None,
) -> list[Row]:
    if section_idx is not None:
        return _rows(await conn.fetch(
            """SELECT * FROM enrollment_assets
               WHERE enrollment_id = $1 AND section_idx = $2
               ORDER BY section_idx, idx""",
            enrollment_id, section_idx,
        ))
    return _rows(await conn.fetch(
        """SELECT * FROM enrollment_assets
           WHERE enrollment_id = $1
           ORDER BY section_idx, idx""",
        enrollment_id,
    ))


# ── users / auth ───────────────────────────────────────────────────────────────

async def create_user(
    conn: asyncpg.Connection, email: str, password_hash: str, username: str = ""
) -> Row:
    uid = new_id()
    clean_email = email.lower().strip()
    uname = username.strip() if username.strip() else clean_email.split("@")[0][:30]
    await conn.execute(
        "INSERT INTO users (id, email, password_hash, email_verified, username) VALUES ($1, $2, $3, 0, $4)",
        uid, clean_email, password_hash, uname,
    )
    return _row(await conn.fetchrow("SELECT * FROM users WHERE id = $1", uid))  # type: ignore[return-value]


async def update_username(
    conn: asyncpg.Connection, user_id: str, username: str
) -> None:
    """Update a user's username. Raises asyncpg.UniqueViolationError if taken."""
    await conn.execute(
        "UPDATE users SET username = $1 WHERE id = $2",
        username, user_id,
    )


async def get_user_by_email(
    conn: asyncpg.Connection, email: str
) -> Row | None:
    return _row(await conn.fetchrow(
        "SELECT * FROM users WHERE email = $1", email.lower().strip()
    ))


async def get_user_by_id(
    conn: asyncpg.Connection, user_id: str
) -> Row | None:
    return _row(await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id))


async def delete_user(conn: asyncpg.Connection, user_id: str) -> None:
    await conn.execute("DELETE FROM users WHERE id = $1", user_id)


async def set_terms_accepted(
    conn: asyncpg.Connection, user_id: str, version: str
) -> None:
    """Record that ``user_id`` has accepted the given Terms of Service version."""
    await conn.execute(
        "UPDATE users SET terms_version_accepted = $1 WHERE id = $2",
        version, user_id,
    )


async def mark_email_verified(
    conn: asyncpg.Connection, user_id: str
) -> None:
    await conn.execute(
        "UPDATE users SET email_verified = 1 WHERE id = $1", user_id
    )


async def create_verification_token(
    conn: asyncpg.Connection,
    user_id: str,
    token: str,
    ttl_hours: int = 24,
) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    async with conn.transaction():
        await conn.execute(
            "DELETE FROM email_verifications WHERE user_id = $1", user_id
        )
        await conn.execute(
            "INSERT INTO email_verifications (token, user_id, expires_at) VALUES ($1, $2, $3)",
            token, user_id, expires_at,
        )


async def consume_verification_token(
    conn: asyncpg.Connection, token: str
) -> str | None:
    """Validate and delete token; returns user_id or None if invalid/expired."""
    async with conn.transaction():
        row = await conn.fetchrow(
            "SELECT user_id FROM email_verifications WHERE token = $1 AND expires_at > CLOCK_TIMESTAMP()",
            token,
        )
        if row is None:
            return None
        user_id = row[0]
        await conn.execute(
            "DELETE FROM email_verifications WHERE token = $1", token
        )
    return user_id


async def create_password_reset_token(
    conn: asyncpg.Connection,
    user_id: str,
    token: str,
    ttl_hours: int = 1,
) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    async with conn.transaction():
        await conn.execute(
            "DELETE FROM password_reset_tokens WHERE user_id = $1", user_id
        )
        await conn.execute(
            "INSERT INTO password_reset_tokens (token, user_id, expires_at) VALUES ($1, $2, $3)",
            token, user_id, expires_at,
        )


async def consume_password_reset_token(
    conn: asyncpg.Connection, token: str
) -> str | None:
    """Validate and delete token; returns user_id or None if invalid/expired."""
    async with conn.transaction():
        row = await conn.fetchrow(
            "SELECT user_id FROM password_reset_tokens WHERE token = $1 AND expires_at > CLOCK_TIMESTAMP()",
            token,
        )
        if row is None:
            return None
        user_id = row[0]
        await conn.execute(
            "DELETE FROM password_reset_tokens WHERE token = $1", token
        )
    return user_id


async def update_password_hash(
    conn: asyncpg.Connection, user_id: str, password_hash: str
) -> None:
    await conn.execute(
        "UPDATE users SET password_hash = $1 WHERE id = $2", password_hash, user_id
    )


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
    conn: asyncpg.Connection,
    admin_emails: tuple[str, ...] | list[str] | set[str] = (),
) -> None:
    normalized = {
        str(email).strip().lower()
        for email in admin_emails
        if str(email).strip()
    }
    for email in normalized:
        await conn.execute(
            "UPDATE users SET is_admin = 1 WHERE email = $1",
            email,
        )


async def get_user_is_admin(conn: asyncpg.Connection, user_id: str) -> bool:
    row = await conn.fetchrow(
        "SELECT is_admin FROM users WHERE id = $1", user_id
    )
    return bool(row and row[0])


async def list_users_for_admin_iam(conn: asyncpg.Connection) -> list[Row]:
    return _rows(await conn.fetch(
        """SELECT id, email, display_name, username, email_verified, is_admin, created_at
           FROM users
           ORDER BY created_at"""
    ))


async def count_admin_users(conn: asyncpg.Connection) -> int:
    val = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_admin = 1")
    return int(val) if val else 0


async def set_user_admin(
    conn: asyncpg.Connection,
    user_id: str,
    *,
    is_admin: bool,
) -> None:
    await conn.execute(
        "UPDATE users SET is_admin = $1 WHERE id = $2",
        1 if is_admin else 0, user_id,
    )


async def seed_personas(conn: asyncpg.Connection) -> None:
    for p in BUILT_IN_PERSONAS:
        await conn.execute(
            """INSERT INTO personas (id, user_id, name, instructions)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT DO NOTHING""",
            p["id"], p["user_id"], p["name"], p["instructions"],
        )


async def get_personas(
    conn: asyncpg.Connection, user_id: str | None = None
) -> list[Row]:
    if user_id:
        return _rows(await conn.fetch(
            "SELECT * FROM personas WHERE user_id IS NULL OR user_id = $1 ORDER BY created_at",
            user_id,
        ))
    return _rows(await conn.fetch(
        "SELECT * FROM personas WHERE user_id IS NULL ORDER BY created_at"
    ))


async def create_persona(
    conn: asyncpg.Connection,
    persona_id: str,
    user_id: str | None,
    name: str,
    instructions: str,
    voice_instructions: str = "",
) -> Row:
    await conn.execute(
        "INSERT INTO personas (id, user_id, name, instructions, voice_instructions) VALUES ($1, $2, $3, $4, $5)",
        persona_id, user_id, name, instructions, voice_instructions,
    )
    return _row(await conn.fetchrow("SELECT * FROM personas WHERE id = $1", persona_id))  # type: ignore[return-value]


async def delete_persona(
    conn: asyncpg.Connection, persona_id: str, user_id: str
) -> bool:
    """Delete only if owned by user_id.  Returns True if deleted."""
    status = await conn.execute(
        "DELETE FROM personas WHERE id = $1 AND user_id = $2",
        persona_id, user_id,
    )
    return status != "DELETE 0"


async def list_all_personas(conn: asyncpg.Connection) -> list[Row]:
    return _rows(await conn.fetch("SELECT * FROM personas ORDER BY created_at"))


async def update_persona(
    conn: asyncpg.Connection,
    persona_id: str,
    name: str,
    instructions: str,
    voice_instructions: str = "",
) -> Row | None:
    await conn.execute(
        "UPDATE personas SET name = $1, instructions = $2, voice_instructions = $3 WHERE id = $4",
        name, instructions, voice_instructions, persona_id,
    )
    return _row(await conn.fetchrow("SELECT * FROM personas WHERE id = $1", persona_id))


async def admin_delete_persona(conn: asyncpg.Connection, persona_id: str) -> bool:
    status = await conn.execute("DELETE FROM personas WHERE id = $1", persona_id)
    return status != "DELETE 0"


# ── points / gamification ───────────────────────────────────────────────────────

async def get_enrollment_section_idx(
    conn: asyncpg.Connection, enrollment_id: str
) -> int:
    val = await conn.fetchval(
        "SELECT current_section_idx FROM lesson_enrollments WHERE id = $1",
        enrollment_id,
    )
    return int(val) if val is not None else 0


async def upsert_user_points(
    conn: asyncpg.Connection,
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
           VALUES ($1, $2, $3, $4, COALESCE($5, 0), COALESCE($6, 0), $7, NOW())
           ON CONFLICT(user_id) DO UPDATE SET
               total_points      = user_points.total_points + excluded.total_points,
               lessons_completed = user_points.lessons_completed + excluded.lessons_completed,
               sections_advanced = user_points.sections_advanced + excluded.sections_advanced,
               current_streak    = CASE WHEN excluded.current_streak != 0 OR $8::int IS NOT NULL
                                        THEN excluded.current_streak
                                        ELSE user_points.current_streak END,
               longest_streak    = CASE WHEN excluded.longest_streak != 0 OR $9::int IS NOT NULL
                                        THEN GREATEST(user_points.longest_streak, excluded.longest_streak)
                                        ELSE user_points.longest_streak END,
               last_lesson_date  = CASE WHEN excluded.last_lesson_date IS NOT NULL
                                        THEN excluded.last_lesson_date
                                        ELSE user_points.last_lesson_date END,
               updated_at        = NOW()""",
        user_id,
        delta_points,
        delta_lessons,
        delta_sections,
        streak,
        longest_streak,
        last_lesson_date,
        streak,          # $8 — streak CASE
        longest_streak,  # $9 — longest_streak CASE
    )


async def get_user_points(
    conn: asyncpg.Connection, user_id: str
) -> Row | None:
    return _row(await conn.fetchrow(
        "SELECT * FROM user_points WHERE user_id = $1", user_id
    ))


async def insert_point_event(
    conn: asyncpg.Connection,
    event_id: str,
    user_id: str,
    event_type: str,
    points: int,
    enrollment_id: str | None = None,
) -> bool:
    """Insert a point event. Returns False if a unique-constrained duplicate was ignored."""
    status = await conn.execute(
        """INSERT INTO point_events (id, user_id, enrollment_id, event_type, points)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT DO NOTHING""",
        event_id, user_id, enrollment_id, event_type, points,
    )
    return status != "INSERT 0 0"


async def get_leaderboard(
    conn: asyncpg.Connection, limit: int = 50
) -> list[Row]:
    return _rows(await conn.fetch(
        """SELECT u.username, u.display_name,
                  up.total_points, up.lessons_completed, up.sections_advanced,
                  up.current_streak, up.longest_streak,
                  ROW_NUMBER() OVER (ORDER BY up.total_points DESC) AS rank
           FROM user_points up
           JOIN users u ON u.id = up.user_id
           WHERE up.total_points > 0 AND up.user_id != $1
           ORDER BY up.total_points DESC
           LIMIT $2""",
        ANON_USER_ID, limit,
    ))


async def get_user_rank(
    conn: asyncpg.Connection, user_id: str
) -> int | None:
    pts = await get_user_points(conn, user_id)
    if pts is None:
        return None
    val = await conn.fetchval(
        """SELECT COUNT(*) + 1
           FROM user_points
           WHERE total_points > (
               SELECT COALESCE(total_points, 0) FROM user_points WHERE user_id = $1
           )
           AND user_id != $2""",
        user_id, ANON_USER_ID,
    )
    return int(val) if val is not None else None


# ── user preferences ──────────────────────────────────────────────────────────

async def get_user_preferences(conn: asyncpg.Connection, user_id: str) -> dict:
    row = await conn.fetchval(
        "SELECT prefs_json FROM user_preferences WHERE user_id = $1", user_id
    )
    if row is None:
        return {}
    return json.loads(row)


async def upsert_user_preferences(
    conn: asyncpg.Connection, user_id: str, prefs: dict
) -> None:
    await conn.execute(
        """INSERT INTO user_preferences (user_id, prefs_json, updated_at)
           VALUES ($1, $2, NOW())
           ON CONFLICT (user_id)
           DO UPDATE SET prefs_json = $2, updated_at = NOW()""",
        user_id, json.dumps(prefs),
    )


# ── pending interactive-tool invocations (Plan B resume support) ─────────────

async def put_pending_tool(
    conn: asyncpg.Connection,
    enrollment_id: str,
    invocation_id: str,
    tool_name: str,
    tool_use_id: str,
    turn_id: str,
    event_payload: dict,
) -> None:
    """Persist (or replace) the single pending interactive tool for an enrollment.

    Called by the dispatcher right before it awaits the student's response.
    Survives WS disconnect / server restart so a different session (or
    different device) can resume the same exercise."""
    await conn.execute(
        """INSERT INTO pending_tool_invocations
               (enrollment_id, invocation_id, tool_name, tool_use_id,
                turn_id, event_payload, created_at)
           VALUES ($1, $2, $3, $4, $5, $6::jsonb, NOW())
           ON CONFLICT (enrollment_id) DO UPDATE SET
               invocation_id = EXCLUDED.invocation_id,
               tool_name     = EXCLUDED.tool_name,
               tool_use_id   = EXCLUDED.tool_use_id,
               turn_id       = EXCLUDED.turn_id,
               event_payload = EXCLUDED.event_payload,
               created_at    = NOW()""",
        enrollment_id, invocation_id, tool_name, tool_use_id,
        turn_id, json.dumps(event_payload),
    )


async def get_pending_tool(
    conn: asyncpg.Connection, enrollment_id: str
) -> Row | None:
    """Look up the pending interactive tool for an enrollment, if any.

    Returns a dict with keys: invocation_id, tool_name, tool_use_id, turn_id,
    event_payload (already parsed from JSONB), created_at.  None if no pending
    tool is recorded."""
    row = await conn.fetchrow(
        """SELECT invocation_id, tool_name, tool_use_id, turn_id,
                  event_payload, created_at
           FROM pending_tool_invocations
           WHERE enrollment_id = $1""",
        enrollment_id,
    )
    if row is None:
        return None
    out = dict(row)
    payload = out.get("event_payload")
    if isinstance(payload, str):
        out["event_payload"] = json.loads(payload)
    return out


async def clear_pending_tool(
    conn: asyncpg.Connection, enrollment_id: str
) -> None:
    """Delete the pending tool row for an enrollment.  Called when the student
    submits or dismisses the tool, or when the agent successfully receives the
    result and incorporates it into the conversation."""
    await conn.execute(
        "DELETE FROM pending_tool_invocations WHERE enrollment_id = $1",
        enrollment_id,
    )
