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
    is_admin       INTEGER NOT NULL DEFAULT 0,
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

-- ── Courses ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS courses (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Lessons ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS lessons (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id           TEXT REFERENCES courses(id) ON DELETE SET NULL,
    title               TEXT NOT NULL,
    description         TEXT,
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

-- ── Password Reset Tokens ─────────────────────────────────────────────────────
-- Short-lived tokens sent by email to allow password reset.

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token      TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Upload Tokens ─────────────────────────────────────────────────────────────
-- Short-lived tokens issued by frontend server for direct PDF upload.

CREATE TABLE IF NOT EXISTS upload_tokens (
    token      TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);

-- ── Usage Tracking ────────────────────────────────────────────────────────────
-- Three-tier: raw events → per-minute (1 month) → per-hour (indefinite).
-- Raw events use a sync SQLite write path (safe from worker threads via WAL).

-- Raw events: one row per API call / STT utterance / TTS synthesis.
-- Pruned to last 48 h after successful aggregation into usage_minutes.
CREATE TABLE IF NOT EXISTS usage_raw (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                 REAL    NOT NULL,            -- Unix timestamp
    user_id            TEXT    NOT NULL DEFAULT '',
    event_type         TEXT    NOT NULL,            -- 'api' | 'stt' | 'tts'
    -- Anthropic API fields
    call_type          TEXT    NOT NULL DEFAULT '', -- decompose_pdf | teach_turn | …
    model              TEXT    NOT NULL DEFAULT '',
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd           REAL    NOT NULL DEFAULT 0,
    -- STT fields
    stt_model          TEXT    NOT NULL DEFAULT '', -- e.g. 'base', 'small'
    stt_language       TEXT    NOT NULL DEFAULT '', -- e.g. 'en', '' = auto
    audio_seconds      REAL    NOT NULL DEFAULT 0,
    transcription_ms   INTEGER NOT NULL DEFAULT 0,
    -- TTS fields
    tts_voice          TEXT    NOT NULL DEFAULT '',
    tts_characters     INTEGER NOT NULL DEFAULT 0,
    tts_audio_seconds  REAL    NOT NULL DEFAULT 0,
    tts_synthesis_ms   INTEGER NOT NULL DEFAULT 0,
    -- Aggregation bookkeeping
    aggregated         INTEGER NOT NULL DEFAULT 0   -- 1 after written to usage_minutes
);
CREATE INDEX IF NOT EXISTS usage_raw_ts          ON usage_raw(ts);
CREATE INDEX IF NOT EXISTS usage_raw_aggregated  ON usage_raw(aggregated, ts);

-- Per-minute aggregates: kept for 1 calendar month, then rolled into usage_hours.
CREATE TABLE IF NOT EXISTS usage_minutes (
    minute_ts          INTEGER NOT NULL,   -- Unix ts truncated to start of minute
    user_id            TEXT    NOT NULL DEFAULT '',
    event_type         TEXT    NOT NULL,
    call_type          TEXT    NOT NULL DEFAULT '',
    model              TEXT    NOT NULL DEFAULT '',
    stt_model          TEXT    NOT NULL DEFAULT '',
    stt_language       TEXT    NOT NULL DEFAULT '',
    tts_voice          TEXT    NOT NULL DEFAULT '',
    -- aggregated counts / sums
    calls              INTEGER NOT NULL DEFAULT 0,
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd           REAL    NOT NULL DEFAULT 0,
    audio_seconds      REAL    NOT NULL DEFAULT 0,
    transcription_ms   INTEGER NOT NULL DEFAULT 0,
    tts_characters     INTEGER NOT NULL DEFAULT 0,
    tts_audio_seconds  REAL    NOT NULL DEFAULT 0,
    tts_synthesis_ms   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (minute_ts, user_id, event_type, call_type, model,
                 stt_model, stt_language, tts_voice)
);
CREATE INDEX IF NOT EXISTS usage_minutes_ts ON usage_minutes(minute_ts);

-- Per-hour aggregates: rolled up from usage_minutes on month boundary; kept forever.
CREATE TABLE IF NOT EXISTS usage_hours (
    hour_ts            INTEGER NOT NULL,   -- Unix ts truncated to start of hour
    user_id            TEXT    NOT NULL DEFAULT '',
    event_type         TEXT    NOT NULL,
    call_type          TEXT    NOT NULL DEFAULT '',
    model              TEXT    NOT NULL DEFAULT '',
    stt_model          TEXT    NOT NULL DEFAULT '',
    stt_language       TEXT    NOT NULL DEFAULT '',
    tts_voice          TEXT    NOT NULL DEFAULT '',
    calls              INTEGER NOT NULL DEFAULT 0,
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd           REAL    NOT NULL DEFAULT 0,
    audio_seconds      REAL    NOT NULL DEFAULT 0,
    transcription_ms   INTEGER NOT NULL DEFAULT 0,
    tts_characters     INTEGER NOT NULL DEFAULT 0,
    tts_audio_seconds  REAL    NOT NULL DEFAULT 0,
    tts_synthesis_ms   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour_ts, user_id, event_type, call_type, model,
                 stt_model, stt_language, tts_voice)
);
CREATE INDEX IF NOT EXISTS usage_hours_ts ON usage_hours(hour_ts);
