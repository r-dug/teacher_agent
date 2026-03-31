"""
One-shot migration: SQLite → PostgreSQL.

Copies all data from storage/db.sqlite3 into the live PostgreSQL database.
Safe to re-run — uses INSERT ... ON CONFLICT DO NOTHING throughout.

Usage:
    /home/appuser/.local/bin/uv run python -m backend.db.migrate_sqlite_to_pg

The PostgreSQL connection is taken from the normal app settings (DATABASE_URL /
PG* env vars).  Run from the project root with the .env loaded, e.g.:

    set -a && source .env && set +a
    /home/appuser/.local/bin/uv run python -m backend.db.migrate_sqlite_to_pg
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

SQLITE_PATH = Path(__file__).parents[2] / "storage" / "db.sqlite3"

# Tables in FK-safe insertion order.
# usage_raw uses BIGINT GENERATED ALWAYS AS IDENTITY — handled specially.
TABLES = [
    "users",
    "sessions",
    "upload_tokens",
    "email_verifications",
    "password_reset_tokens",
    "personas",
    "courses",
    "course_source_files",
    "course_chapter_drafts",
    "textbook_toc_cache",
    "decomposition_cache",
    "lessons",
    "course_chapter_lessons",
    "course_advisor_sessions",
    "course_decomposition_jobs",
    "course_decomposition_job_items",
    "course_publish_copies",
    "lesson_publish_copies",
    "lesson_enrollments",
    "lesson_sections",
    "section_assets",
    "enrollment_assets",
    "messages",
    "usage_minutes",
    "usage_hours",
    "usage_raw",
    "user_points",
    "point_events",
]

# Columns to skip when inserting (e.g. auto-generated identity columns).
SKIP_COLS: dict[str, set[str]] = {
    "usage_raw": {"id"},          # BIGINT GENERATED ALWAYS AS IDENTITY
    "sqlite_sequence": {"*"},     # internal SQLite table — never migrate
    "lessons": {"lesson_goal"},   # moved to lesson_enrollments in schema v2
}


def _coerce_value(value):
    """Convert SQLite timestamp strings to datetime objects for asyncpg TIMESTAMPTZ columns."""
    if not isinstance(value, str):
        return value
    # Try common SQLite datetime formats
    from datetime import datetime, timezone
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return value  # not a timestamp string — return as-is


async def migrate() -> None:
    from backend.config import settings

    print(f"[migrate] SQLite source : {SQLITE_PATH}")
    print(f"[migrate] PostgreSQL URL: {settings.DATABASE_URL}")

    if not SQLITE_PATH.exists():
        print(f"[migrate] ERROR: SQLite file not found at {SQLITE_PATH}", file=sys.stderr)
        sys.exit(1)

    sqlite_con = sqlite3.connect(str(SQLITE_PATH))
    sqlite_con.row_factory = sqlite3.Row

    import asyncpg

    password_fn = settings.pg_password if settings.IS_AZURE_PG else None
    pg_kwargs: dict = {"ssl": "require"} if settings.IS_AZURE_PG else {}
    if password_fn:
        pg_kwargs["password"] = password_fn

    pg = await asyncpg.connect(settings.DATABASE_URL, **pg_kwargs)

    try:
        for table in TABLES:
            skip = SKIP_COLS.get(table, set())
            if "*" in skip:
                continue

            rows = sqlite_con.execute(f'SELECT * FROM "{table}"').fetchall()
            if not rows:
                print(f"[migrate] {table}: 0 rows — skipped")
                continue

            all_cols = [d[0] for d in sqlite_con.execute(f'SELECT * FROM "{table}" LIMIT 0').description]
            cols = [c for c in all_cols if c not in skip]

            placeholders = ", ".join(f"${i+1}" for i in range(len(cols)))
            col_list = ", ".join(f'"{c}"' for c in cols)
            sql = (
                f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders}) '
                f'ON CONFLICT DO NOTHING'
            )

            records = [tuple(_coerce_value(row[c]) for c in cols) for row in rows]

            try:
                await pg.executemany(sql, records)
                print(f"[migrate] {table}: {len(records)} rows inserted (duplicates skipped)")
            except Exception:
                # Fall back to row-by-row to skip FK violations / bad rows
                ok = skipped = 0
                for record in records:
                    try:
                        await pg.execute(sql, *record)
                        ok += 1
                    except Exception as row_exc:
                        skipped += 1
                        if skipped <= 5:
                            print(f"[migrate]   skip row {record[0]}: {row_exc}")
                print(f"[migrate] {table}: {ok} inserted, {skipped} skipped")

    finally:
        sqlite_con.close()
        await pg.close()

    print("[migrate] Done.")


if __name__ == "__main__":
    asyncio.run(migrate())
