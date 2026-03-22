---
name: db-debug
description: Inspect and query the SQLite database directly. Use when debugging data state, checking records, or investigating schema issues.
allowed-tools: Bash, Read
---

The SQLite database is at `storage/db.sqlite3`.
Schema is defined in `backend/db/schema.sql`.

## Quick one-liners:

```bash
# List all tables
sqlite3 storage/db.sqlite3 ".tables"

# Describe a table's schema
sqlite3 storage/db.sqlite3 ".schema courses"

# Count rows in a table
sqlite3 storage/db.sqlite3 "SELECT COUNT(*) FROM lessons;"

# Recent courses
sqlite3 storage/db.sqlite3 "SELECT id, title, visibility, created_at FROM courses ORDER BY created_at DESC LIMIT 10;"

# Recent lessons
sqlite3 storage/db.sqlite3 "SELECT id, title, visibility, created_at FROM lessons ORDER BY created_at DESC LIMIT 10;"

# Decomposition jobs for a course
sqlite3 storage/db.sqlite3 "SELECT id, status, decompose_mode, total_items, completed_items, failed_items, created_at FROM course_decomposition_jobs WHERE course_id = '$COURSE_ID' ORDER BY created_at DESC LIMIT 5;"

# Chapter drafts for a course
sqlite3 storage/db.sqlite3 "SELECT idx, title, page_start, page_end, included FROM course_chapter_drafts WHERE course_id = '$COURSE_ID' ORDER BY idx;"

# Advisor session state
sqlite3 storage/db.sqlite3 "SELECT status, objectives_prompt FROM course_advisor_sessions WHERE course_id = '$COURSE_ID';"

# Active sessions
sqlite3 storage/db.sqlite3 "SELECT s.id, u.email, s.created_at, s.last_seen FROM sessions s JOIN users u ON u.id = s.user_id ORDER BY s.last_seen DESC LIMIT 10;"

# Lesson sections for a lesson
sqlite3 storage/db.sqlite3 "SELECT idx, title, page_start, page_end FROM lesson_sections WHERE lesson_id = '$LESSON_ID' ORDER BY idx;"

# Recent usage costs
sqlite3 storage/db.sqlite3 "SELECT event_type, call_type, model, SUM(cost_usd) as total_cost, SUM(input_tokens) as total_in, SUM(output_tokens) as total_out FROM usage_raw GROUP BY event_type, call_type, model ORDER BY total_cost DESC LIMIT 20;"
```

## Understand $ARGUMENTS and respond accordingly:

- **Empty** — run `.tables` and give a high-level summary of what's in the DB (row counts per major table).

- **A table name** (e.g. `courses`, `lessons`) — show schema + recent rows.

- **A course/lesson ID** — show all relevant records for that entity (course, chapters, advisor session, decomposition jobs).

- **A SQL query** — run it directly and show the result.

- **A concept** (e.g. `decomposition`, `enrollment`, `usage`) — inspect the relevant tables.

## Always use `-column -header` for readability:
```bash
sqlite3 -column -header storage/db.sqlite3 "SELECT ..."
```

## If the DB doesn't exist:
Tell the user to start the backend once so it initializes the schema.
