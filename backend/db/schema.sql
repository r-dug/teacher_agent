-- pdf-to-audio database schema
-- Designed for SQLite (prototype); Postgres-compatible types throughout.
-- All primary keys are UUID strings.  Booleans are 0/1 integers (SQLite).
-- Timestamps are ISO-8601 strings produced by datetime('now').

PRAGMA journal_mode = WAL;   -- enables concurrent readers
PRAGMA foreign_keys = ON;

-- ── Users ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id             TEXT PRIMARY KEY,
    email          TEXT UNIQUE,
    display_name   TEXT,
    password_hash  TEXT,
    email_verified INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Email Verifications ────────────────────────────────────────────────────────
-- Short-lived tokens sent by email to confirm ownership.

CREATE TABLE IF NOT EXISTS email_verifications (
    token      TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Sessions ──────────────────────────────────────────────────────────────────
-- id is also the opaque bearer token sent by the frontend server.

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Lessons ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS lessons (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title               TEXT NOT NULL,
    -- Relative path under STORAGE_DIR, e.g. "{user_id}/pdfs/{lesson_id}.pdf"
    pdf_path            TEXT,
    current_section_idx INTEGER NOT NULL DEFAULT 0,
    completed           INTEGER NOT NULL DEFAULT 0,   -- boolean (0/1)
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Lesson Sections ───────────────────────────────────────────────────────────
-- Populated after PDF decomposition.

CREATE TABLE IF NOT EXISTS lesson_sections (
    id        TEXT PRIMARY KEY,
    lesson_id TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    idx       INTEGER NOT NULL,
    title     TEXT,
    content   TEXT NOT NULL,
    -- JSON array of key concept strings, e.g. '["gravity", "mass"]'
    key_concepts TEXT NOT NULL DEFAULT '[]',
    page_start   INTEGER,
    page_end     INTEGER,
    UNIQUE (lesson_id, idx)
);

-- ── Conversation Messages ─────────────────────────────────────────────────────
-- content is JSON-serialised to match Anthropic SDK message format.

CREATE TABLE IF NOT EXISTS messages (
    id        TEXT PRIMARY KEY,
    lesson_id TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    idx       INTEGER NOT NULL,
    role      TEXT NOT NULL,    -- 'user' | 'assistant' | 'tool' | 'tool_result'
    content   TEXT NOT NULL,    -- JSON string
    UNIQUE (lesson_id, idx)
);

-- ── Teaching Personas ─────────────────────────────────────────────────────────
-- user_id NULL = built-in / global persona visible to all users.

CREATE TABLE IF NOT EXISTS personas (
    id           TEXT PRIMARY KEY,   -- slug, e.g. 'socratic'
    user_id      TEXT REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    instructions TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Upload Tokens ─────────────────────────────────────────────────────────────
-- Short-lived tokens issued by frontend server for direct PDF upload.

CREATE TABLE IF NOT EXISTS upload_tokens (
    token      TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
