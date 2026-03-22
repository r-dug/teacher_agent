"""Migration v3 — add enrollment_assets table.

Run once against an existing database that was created before this table
existed.  Safe to run multiple times (CREATE TABLE IF NOT EXISTS).

    uv run python -m backend.db.migrate_v3
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_DDL = """
CREATE TABLE IF NOT EXISTS enrollment_assets (
    id            TEXT PRIMARY KEY,
    enrollment_id TEXT NOT NULL REFERENCES lesson_enrollments(id) ON DELETE CASCADE,
    section_idx   INTEGER NOT NULL,
    asset_type    TEXT NOT NULL DEFAULT 'ai_image',
    image_path    TEXT,
    prompt        TEXT,
    revised_prompt TEXT,
    tool_use_id   TEXT,
    idx           INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_enrollment_assets_enrollment
    ON enrollment_assets(enrollment_id, section_idx);
"""


def run(db_path: Path) -> None:
    print(f"[migrate_v3] connecting to {db_path}")
    con = sqlite3.connect(db_path)
    con.executescript(_DDL)
    con.commit()
    con.close()
    print("[migrate_v3] done — enrollment_assets table ready.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        from ..config import settings  # type: ignore[import]
        path = settings.DB_PATH
    run(path)
