# Database

PostgreSQL database layer using raw SQL via `asyncpg`. No ORM — queries are transparent and explicit.

## Files

```
db/
  schema.sql        Full DDL — tables, indexes, constraints
  models.py         Typed async query helpers (all accept asyncpg.Connection)
  connection.py     Connection pool setup, startup/shutdown
```

## Schema Overview

### Core Teaching

| Table | Purpose |
|-------|---------|
| `lessons` | Lesson metadata (title, PDF path, creator, visibility) |
| `lesson_sections` | Ordered sections per lesson (title, content, key_concepts JSON, page range) |
| `lesson_enrollments` | Per-user enrollment state (section index, completion, task_progress JSON, goal) |
| `messages` | Conversation history per enrollment (role, content JSON, ordered by idx) |
| `enrollment_assets` | AI-generated images tied to enrollments (section, prompt, image path) |
| `section_assets` | Pre-extracted assets from PDF pages (images, captions) |

### Courses

| Table | Purpose |
|-------|---------|
| `courses` | Course metadata (title, creator, visibility) |
| `course_chapter_drafts` | Draft chapters within a course |
| `course_chapter_lessons` | Lesson ordering within chapters |

### Users & Auth

| Table | Purpose |
|-------|---------|
| `users` | User accounts (email, password hash, role, verification) |

### Gamification

| Table | Purpose |
|-------|---------|
| `user_points` | Aggregate point totals, streaks, lesson counts |
| `point_events` | Individual point award events |

### Tracking

| Table | Purpose |
|-------|---------|
| `usage_raw` | Per-request token/cost tracking |
| `usage_minutes` / `usage_hours` | Aggregated usage rollups |
| `user_preferences` | User settings (JSON) |

## Query Pattern

All query functions in `models.py` follow the same pattern:

```python
async def get_thing(conn: asyncpg.Connection, id: str) -> Row | None:
    return _row(await conn.fetchrow("SELECT * FROM things WHERE id = $1", id))
```

- `_row()` converts asyncpg Records to plain dicts
- `_rows()` does the same for lists
- Parameter style: `$1, $2, ...` (PostgreSQL native)
- Write operations use explicit transactions where needed

## Migrations

Schema changes use idempotent statements at the end of `schema.sql`:

```sql
ALTER TABLE lesson_enrollments ADD COLUMN IF NOT EXISTS task_progress TEXT NOT NULL DEFAULT '{}';
```

PostgreSQL's `ADD COLUMN IF NOT EXISTS` makes these safe to re-run on every startup. The full `CREATE TABLE IF NOT EXISTS` blocks handle fresh installs.

## Key Design Decisions

- **JSON columns** for flexible data: `key_concepts` (array of strings), `task_progress` (nested object), `content` (message blocks), `prefs_json` (user settings)
- **No ORM** — raw SQL keeps queries visible and avoids N+1 surprises
- **Enrollment model** — lessons are templates; per-user state lives in `lesson_enrollments` + `messages`
