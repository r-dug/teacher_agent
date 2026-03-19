"""
Migration v2: template/enrollment data model.

Changes
-------
- courses.user_id            → creator_id
- lessons.user_id            → creator_id
- lessons: add visibility, drop current_section_idx / completed / lesson_goal
- course_source_files.user_id  → creator_id
- course_advisor_sessions.user_id → creator_id
- course_decomposition_jobs.user_id → creator_id
- New table: lesson_enrollments (per-user progress state)
- messages.lesson_id         → enrollment_id (FK to lesson_enrollments)
- New table: section_assets
- Drop tables: course_publish_copies, lesson_publish_copies
- PDF storage: {user_id}/pdfs/{lesson_id}.pdf → lessons/{lesson_id}.pdf
- PDF storage: {user_id}/course_sources/{course_id}.pdf → courses/{course_id}.pdf

Usage
-----
    python -m backend.db.migrate_v2 [--db path/to/app.db] [--storage path/to/storage]

Defaults: STORAGE_DIR from settings, db path from settings.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
from pathlib import Path


async def run(db_path: Path, storage_dir: Path) -> None:
    import aiosqlite

    print(f"[migrate_v2] DB: {db_path}")
    print(f"[migrate_v2] Storage: {storage_dir}")

    async with aiosqlite.connect(str(db_path)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode = WAL")
        await conn.execute("PRAGMA foreign_keys = OFF")  # disable during migration

        # ── 1. Rename user_id → creator_id on authoring tables ─────────────────

        for table, col in [
            ("courses", "user_id"),
            ("lessons", "user_id"),
            ("course_source_files", "user_id"),
            ("course_advisor_sessions", "user_id"),
            ("course_decomposition_jobs", "user_id"),
        ]:
            col_exists = await _col_exists(conn, table, col)
            creator_exists = await _col_exists(conn, table, "creator_id")
            if col_exists and not creator_exists:
                print(f"[migrate_v2] {table}: RENAME COLUMN {col} → creator_id")
                await conn.execute(
                    f"ALTER TABLE {table} RENAME COLUMN {col} TO creator_id"
                )

        # ── 2. Add visibility to courses and lessons ────────────────────────────

        for table in ("courses", "lessons"):
            if not await _col_exists(conn, table, "visibility"):
                print(f"[migrate_v2] {table}: ADD COLUMN visibility TEXT DEFAULT 'draft'")
                await conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN visibility TEXT NOT NULL DEFAULT 'draft'"
                )

        # ── 3. Create lesson_enrollments ────────────────────────────────────────

        print("[migrate_v2] CREATE TABLE lesson_enrollments (if not exists)")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS lesson_enrollments (
                id                  TEXT PRIMARY KEY,
                lesson_id           TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
                user_id             TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                current_section_idx INTEGER NOT NULL DEFAULT 0,
                completed           INTEGER NOT NULL DEFAULT 0,
                lesson_goal         TEXT,
                created_at          TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (lesson_id, user_id)
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lesson_enrollments_user ON lesson_enrollments(user_id)"
        )

        # ── 4. Create enrollment rows from existing lesson state ────────────────
        # Each lesson row currently holds state (current_section_idx, completed, lesson_goal)
        # for the lesson's creator.  Migrate this into an enrollment row.

        has_section_idx = await _col_exists(conn, "lessons", "current_section_idx")
        has_completed = await _col_exists(conn, "lessons", "completed")
        has_goal = await _col_exists(conn, "lessons", "lesson_goal")

        if has_section_idx or has_completed or has_goal:
            print("[migrate_v2] Migrating lesson state → lesson_enrollments")
            select_cols = "id, creator_id"
            if has_section_idx:
                select_cols += ", current_section_idx"
            if has_completed:
                select_cols += ", completed"
            if has_goal:
                select_cols += ", lesson_goal"

            async with conn.execute(f"SELECT {select_cols} FROM lessons") as cur:
                lesson_rows = await cur.fetchall()

            import uuid
            for row in lesson_rows:
                row_d = dict(row)
                lesson_id = row_d["id"]
                user_id = row_d["creator_id"]
                # Check if enrollment already exists
                async with conn.execute(
                    "SELECT id FROM lesson_enrollments WHERE lesson_id = ? AND user_id = ?",
                    (lesson_id, user_id),
                ) as cur2:
                    existing = await cur2.fetchone()
                if existing:
                    continue
                eid = str(uuid.uuid4())
                sec_idx = row_d.get("current_section_idx", 0) or 0
                completed = row_d.get("completed", 0) or 0
                goal = row_d.get("lesson_goal") or None
                await conn.execute(
                    """INSERT INTO lesson_enrollments
                       (id, lesson_id, user_id, current_section_idx, completed, lesson_goal)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (eid, lesson_id, user_id, sec_idx, completed, goal),
                )

        # ── 5. Also create enrollments from lesson_publish_copies ───────────────
        # Students who had lesson copies published to them get enrollment rows
        # pointing to the SOURCE lesson.

        if await _table_exists(conn, "lesson_publish_copies"):
            print("[migrate_v2] Migrating lesson_publish_copies → lesson_enrollments")
            async with conn.execute(
                "SELECT source_lesson_id, target_user_id, target_lesson_id FROM lesson_publish_copies"
            ) as cur:
                copy_rows = await cur.fetchall()

            import uuid as _uuid
            for row in copy_rows:
                src_lesson_id = row[0]
                student_user_id = row[1]
                target_lesson_id = row[2]

                # Check source lesson still exists
                async with conn.execute(
                    "SELECT id FROM lessons WHERE id = ?", (src_lesson_id,)
                ) as c2:
                    if not await c2.fetchone():
                        continue

                # Get state from the target (student-owned) lesson if it exists
                sec_idx, completed, goal = 0, 0, None
                if await _table_exists(conn, "lessons"):
                    has_si = await _col_exists(conn, "lessons", "current_section_idx")
                    async with conn.execute(
                        "SELECT * FROM lessons WHERE id = ?", (target_lesson_id,)
                    ) as c3:
                        trow = await c3.fetchone()
                    if trow:
                        trow_d = dict(trow)
                        if has_si:
                            sec_idx = trow_d.get("current_section_idx", 0) or 0
                            completed = trow_d.get("completed", 0) or 0
                            goal = trow_d.get("lesson_goal") or None

                # Enrollment on source lesson
                async with conn.execute(
                    "SELECT id FROM lesson_enrollments WHERE lesson_id = ? AND user_id = ?",
                    (src_lesson_id, student_user_id),
                ) as c4:
                    if await c4.fetchone():
                        continue

                eid = str(_uuid.uuid4())
                await conn.execute(
                    """INSERT INTO lesson_enrollments
                       (id, lesson_id, user_id, current_section_idx, completed, lesson_goal)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (eid, src_lesson_id, student_user_id, sec_idx, completed, goal),
                )

        # ── 6. Migrate messages table: lesson_id → enrollment_id ───────────────

        has_lesson_id_col = await _col_exists(conn, "messages", "lesson_id")
        if has_lesson_id_col:
            print("[migrate_v2] Migrating messages.lesson_id → enrollment_id")

            # Build lesson_id → enrollment_id map (using creator's enrollment as default)
            async with conn.execute(
                "SELECT id, lesson_id FROM lesson_enrollments"
            ) as cur:
                enrollment_rows = await cur.fetchall()

            # Map: (lesson_id, user_id) → enrollment_id
            # For messages, we use a simpler mapping: lesson_id → best enrollment
            # Since old messages belonged to the lesson's creator, use creator's enrollment.
            async with conn.execute(
                "SELECT id, creator_id FROM lessons"
            ) as cur:
                lesson_creator_map = {row[0]: row[1] for row in await cur.fetchall()}

            enrollment_map: dict[str, str] = {}  # lesson_id → enrollment_id
            for row in enrollment_rows:
                eid, lid = row[0], row[1]
                creator_id = lesson_creator_map.get(lid)
                # Prefer enrollment owned by the creator
                async with conn.execute(
                    "SELECT user_id FROM lesson_enrollments WHERE id = ?", (eid,)
                ) as c2:
                    erow = await c2.fetchone()
                if erow and erow[0] == creator_id:
                    enrollment_map[lid] = eid
                elif lid not in enrollment_map:
                    enrollment_map[lid] = eid

            # Create new messages table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS messages_new (
                    id            TEXT PRIMARY KEY,
                    enrollment_id TEXT NOT NULL REFERENCES lesson_enrollments(id) ON DELETE CASCADE,
                    idx           INTEGER NOT NULL,
                    role          TEXT NOT NULL,
                    content       TEXT NOT NULL,
                    UNIQUE (enrollment_id, idx)
                )
            """)

            import uuid as _uuid2
            async with conn.execute(
                "SELECT id, lesson_id, idx, role, content FROM messages"
            ) as cur:
                msg_rows = await cur.fetchall()

            for row in msg_rows:
                mid, lesson_id, idx, role, content = row
                enrollment_id = enrollment_map.get(lesson_id)
                if enrollment_id is None:
                    print(f"[migrate_v2] WARNING: no enrollment for lesson {lesson_id}, dropping messages")
                    continue
                await conn.execute(
                    "INSERT OR IGNORE INTO messages_new (id, enrollment_id, idx, role, content) VALUES (?, ?, ?, ?, ?)",
                    (mid, enrollment_id, idx, role, content),
                )

            await conn.execute("DROP TABLE messages")
            await conn.execute("ALTER TABLE messages_new RENAME TO messages")
            print(f"[migrate_v2] Migrated {len(msg_rows)} messages")

        # ── 7. Create section_assets table ─────────────────────────────────────

        print("[migrate_v2] CREATE TABLE section_assets (if not exists)")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS section_assets (
                id         TEXT PRIMARY KEY,
                section_id TEXT NOT NULL REFERENCES lesson_sections(id) ON DELETE CASCADE,
                asset_type TEXT NOT NULL,
                page_start INTEGER,
                page_end   INTEGER,
                image_path TEXT,
                caption    TEXT,
                idx        INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (section_id, idx)
            )
        """)

        # ── 8. Drop publish copy tables ─────────────────────────────────────────

        for table in ("course_publish_copies", "lesson_publish_copies"):
            if await _table_exists(conn, table):
                print(f"[migrate_v2] DROP TABLE {table}")
                await conn.execute(f"DROP TABLE {table}")

        # ── 9. Drop stale enrollment-state columns from lessons ─────────────────
        # SQLite 3.35+ supports DROP COLUMN, but only if no index/trigger references it.

        for col in ("current_section_idx", "completed", "lesson_goal"):
            if await _col_exists(conn, "lessons", col):
                try:
                    await conn.execute(f"ALTER TABLE lessons DROP COLUMN {col}")
                    print(f"[migrate_v2] lessons: DROP COLUMN {col}")
                except Exception as exc:
                    print(f"[migrate_v2] WARNING: could not drop lessons.{col}: {exc}")

        await conn.commit()
        print("[migrate_v2] DB schema migration complete.")

    # ── 10. Move PDF files on disk ──────────────────────────────────────────────

    # We need a fresh connection to read updated lesson/course data.
    async with aiosqlite.connect(str(db_path)) as conn2:
        conn2.row_factory = aiosqlite.Row
        await _migrate_pdf_files(conn2, storage_dir)

    print("[migrate_v2] Done.")


async def _migrate_pdf_files(
    conn: "aiosqlite.Connection", storage_dir: Path
) -> None:
    """Move PDFs to new storage layout and update db paths."""
    import aiosqlite

    print("[migrate_v2] Migrating PDF file paths...")

    # Lessons: {user_id}/pdfs/{lesson_id}.pdf → lessons/{lesson_id}.pdf
    async with conn.execute("SELECT id, pdf_path FROM lessons WHERE pdf_path IS NOT NULL") as cur:
        lessons = await cur.fetchall()

    for row in lessons:
        lesson_id, old_rel = row[0], row[1]
        if not old_rel:
            continue
        new_rel = f"lessons/{lesson_id}.pdf"
        if old_rel == new_rel:
            continue
        src = storage_dir / old_rel
        dst = storage_dir / new_rel
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            await conn.execute(
                "UPDATE lessons SET pdf_path = ? WHERE id = ?", (new_rel, lesson_id)
            )
            print(f"[migrate_v2] lesson {lesson_id}: {old_rel} → {new_rel}")
        else:
            print(f"[migrate_v2] WARNING: lesson {lesson_id}: source PDF not found at {src}")

    # Course source files: {user_id}/course_sources/{course_id}.pdf → courses/{course_id}.pdf
    async with conn.execute("SELECT course_id, pdf_path FROM course_source_files") as cur:
        sources = await cur.fetchall()

    for row in sources:
        course_id, old_rel = row[0], row[1]
        if not old_rel:
            continue
        new_rel = f"courses/{course_id}.pdf"
        if old_rel == new_rel:
            continue
        src = storage_dir / old_rel
        dst = storage_dir / new_rel
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            await conn.execute(
                "UPDATE course_source_files SET pdf_path = ? WHERE course_id = ?",
                (new_rel, course_id),
            )
            print(f"[migrate_v2] course_source {course_id}: {old_rel} → {new_rel}")
        else:
            print(f"[migrate_v2] WARNING: course {course_id}: source PDF not found at {src}")

    await conn.commit()


async def _col_exists(conn, table: str, col: str) -> bool:
    import aiosqlite
    async with conn.execute(f"PRAGMA table_info({table})") as cur:
        rows = await cur.fetchall()
    return any(row[1] == col for row in rows)


async def _table_exists(conn, table: str) -> bool:
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ) as cur:
        return await cur.fetchone() is not None


def main() -> None:
    parser = argparse.ArgumentParser(description="pdf-to-audio database migration v2")
    parser.add_argument("--db", type=Path, default=None, help="Path to SQLite DB file")
    parser.add_argument("--storage", type=Path, default=None, help="Path to STORAGE_DIR")
    args = parser.parse_args()

    from backend.config import settings

    db_path = args.db or Path(str(settings.DB_PATH))
    storage_dir = args.storage or settings.STORAGE_DIR

    asyncio.run(run(db_path, storage_dir))


if __name__ == "__main__":
    main()
