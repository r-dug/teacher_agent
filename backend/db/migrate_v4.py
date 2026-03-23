"""Migration v4 — gamification: username, user_points, point_events.

Changes applied (all idempotent / safe to re-run):

1. users: add username column (TEXT UNIQUE, auto-populated from email prefix)
2. CREATE TABLE user_points   — single running total + stats per user
3. CREATE TABLE point_events  — append-only audit log with dedup index

Run:
    uv run python -m backend.db.migrate_v4
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path


def _col_names(con: sqlite3.Connection, table: str) -> set[str]:
    cur = con.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def _slugify(text: str) -> str:
    """Convert an email prefix into a safe username candidate."""
    slug = re.sub(r"[^a-zA-Z0-9_]", "_", text)
    return slug[:30].strip("_") or "user"


def run(db_path: Path) -> None:
    print(f"[migrate_v4] connecting to {db_path}")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    # ── 1. users.username ────────────────────────────────────────────────────
    cols = _col_names(con, "users")
    if "username" not in cols:
        con.execute("ALTER TABLE users ADD COLUMN username TEXT")
        print("[migrate_v4] users: added username column")

        # Populate existing rows with a unique slug derived from their email.
        rows = con.execute("SELECT id, email FROM users").fetchall()
        used: set[str] = set()
        for row in rows:
            uid, email = row["id"], row["email"] or ""
            base = _slugify(email.split("@")[0])
            candidate = base
            suffix = 1
            while candidate in used:
                candidate = f"{base}_{suffix}"
                suffix += 1
            used.add(candidate)
            con.execute("UPDATE users SET username = ? WHERE id = ?", (candidate, uid))
        print(f"[migrate_v4] users: populated usernames for {len(rows)} existing rows")

        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username)"
        )
        print("[migrate_v4] users: created unique index on username")
    else:
        print("[migrate_v4] users: username column — skipped")

    # ── 2. user_points ───────────────────────────────────────────────────────
    con.executescript("""
        CREATE TABLE IF NOT EXISTS user_points (
            user_id             TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            total_points        INTEGER NOT NULL DEFAULT 0,
            lessons_completed   INTEGER NOT NULL DEFAULT 0,
            sections_advanced   INTEGER NOT NULL DEFAULT 0,
            current_streak      INTEGER NOT NULL DEFAULT 0,
            longest_streak      INTEGER NOT NULL DEFAULT 0,
            last_lesson_date    TEXT,
            updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    print("[migrate_v4] user_points — ok")

    # ── 3. point_events ──────────────────────────────────────────────────────
    con.executescript("""
        CREATE TABLE IF NOT EXISTS point_events (
            id            TEXT PRIMARY KEY,
            user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            enrollment_id TEXT REFERENCES lesson_enrollments(id) ON DELETE SET NULL,
            event_type    TEXT NOT NULL,
            points        INTEGER NOT NULL,
            created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_point_events_user
            ON point_events(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_point_events_enrollment
            ON point_events(enrollment_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_point_events_lesson_complete
            ON point_events(enrollment_id, event_type)
            WHERE event_type = 'lesson_complete';
    """)
    print("[migrate_v4] point_events — ok")

    con.commit()
    con.close()
    print("[migrate_v4] done.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        from ..config import settings  # type: ignore[import]
        path = settings.DB_PATH
    run(path)
