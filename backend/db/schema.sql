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
-- Admin-authored templates. visibility='published' makes them accessible to all users.

CREATE TABLE IF NOT EXISTS courses (
    id          TEXT PRIMARY KEY,
    creator_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    description TEXT,
    visibility  TEXT NOT NULL DEFAULT 'draft',  -- 'draft' | 'published'
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Textbook Authoring ───────────────────────────────────────────────────────
-- course_source_files stores the original textbook upload for a course.
-- course_chapter_drafts stores editable chapter page ranges before full decomposition.
-- textbook_toc_cache caches TOC-derived chapter outlines by PDF hash.
-- decomposition_cache is the future section-cache store keyed by hash+range+prompt fingerprint.

CREATE TABLE IF NOT EXISTS course_source_files (
    course_id   TEXT PRIMARY KEY REFERENCES courses(id) ON DELETE CASCADE,
    creator_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    pdf_hash    TEXT NOT NULL,
    pdf_path    TEXT NOT NULL,
    page_count  INTEGER NOT NULL,
    toc_json    TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_course_source_files_pdf_hash ON course_source_files(pdf_hash);

CREATE TABLE IF NOT EXISTS course_chapter_drafts (
    id          TEXT PRIMARY KEY,
    course_id   TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    title       TEXT NOT NULL,
    page_start  INTEGER NOT NULL,
    page_end    INTEGER NOT NULL,
    included    INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(course_id, idx)
);
CREATE INDEX IF NOT EXISTS idx_course_chapter_drafts_course ON course_chapter_drafts(course_id, idx);

CREATE TABLE IF NOT EXISTS textbook_toc_cache (
    pdf_hash      TEXT PRIMARY KEY,
    page_count    INTEGER NOT NULL,
    toc_json      TEXT NOT NULL DEFAULT '[]',
    chapters_json TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS decomposition_cache (
    cache_key       TEXT PRIMARY KEY,
    pdf_hash        TEXT NOT NULL,
    page_start      INTEGER NOT NULL,
    page_end        INTEGER NOT NULL,
    objectives_hash TEXT NOT NULL DEFAULT '',
    model           TEXT NOT NULL DEFAULT '',
    prompt_version  TEXT NOT NULL DEFAULT '',
    sections_json   TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_decomposition_cache_lookup
    ON decomposition_cache(pdf_hash, page_start, page_end, objectives_hash, model, prompt_version);

CREATE TABLE IF NOT EXISTS course_chapter_lessons (
    course_id    TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    chapter_id   TEXT NOT NULL REFERENCES course_chapter_drafts(id) ON DELETE CASCADE,
    lesson_id    TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (course_id, chapter_id)
);
CREATE INDEX IF NOT EXISTS idx_course_chapter_lessons_lesson ON course_chapter_lessons(lesson_id);

CREATE TABLE IF NOT EXISTS course_advisor_sessions (
    course_id          TEXT PRIMARY KEY REFERENCES courses(id) ON DELETE CASCADE,
    creator_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    transcript_json    TEXT NOT NULL DEFAULT '[]',
    objectives_prompt  TEXT,
    status             TEXT NOT NULL DEFAULT 'draft',  -- draft | finalized
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS course_decomposition_jobs (
    id                TEXT PRIMARY KEY,
    course_id         TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    creator_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status            TEXT NOT NULL DEFAULT 'queued', -- queued | running | completed | failed
    decompose_mode    TEXT NOT NULL DEFAULT 'pdf',    -- 'pdf' | 'text'
    objectives_prompt TEXT NOT NULL DEFAULT '',
    total_items       INTEGER NOT NULL DEFAULT 0,
    completed_items   INTEGER NOT NULL DEFAULT 0,
    failed_items      INTEGER NOT NULL DEFAULT 0,
    notify_session_id TEXT,
    error             TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    started_at        TEXT,
    finished_at       TEXT,
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_course_decomp_jobs_course ON course_decomposition_jobs(course_id, created_at);

CREATE TABLE IF NOT EXISTS course_decomposition_job_items (
    id           TEXT PRIMARY KEY,
    job_id       TEXT NOT NULL REFERENCES course_decomposition_jobs(id) ON DELETE CASCADE,
    chapter_id   TEXT NOT NULL REFERENCES course_chapter_drafts(id) ON DELETE CASCADE,
    idx          INTEGER NOT NULL,
    title        TEXT NOT NULL,
    page_start   INTEGER NOT NULL,
    page_end     INTEGER NOT NULL,
    lesson_id    TEXT,
    cache_key    TEXT,
    status       TEXT NOT NULL DEFAULT 'queued', -- queued | running | completed | cached | failed
    error        TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_course_decomp_items_job ON course_decomposition_job_items(job_id, idx);

-- ── Lessons ───────────────────────────────────────────────────────────────────
-- Admin-authored templates.  Per-user state lives in lesson_enrollments.
-- pdf_path is relative to STORAGE_DIR: 'lessons/{lesson_id}.pdf'

CREATE TABLE IF NOT EXISTS lessons (
    id          TEXT PRIMARY KEY,
    creator_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id   TEXT REFERENCES courses(id) ON DELETE SET NULL,
    title       TEXT NOT NULL,
    description TEXT,
    pdf_path    TEXT,
    visibility  TEXT NOT NULL DEFAULT 'draft',  -- 'draft' | 'published'
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Lesson Enrollments ────────────────────────────────────────────────────────
-- Per-user progress state for a lesson.  Created lazily on first WS connect.

CREATE TABLE IF NOT EXISTS lesson_enrollments (
    id                  TEXT PRIMARY KEY,
    lesson_id           TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    user_id             TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    current_section_idx INTEGER NOT NULL DEFAULT 0,
    completed           INTEGER NOT NULL DEFAULT 0,   -- boolean (0/1)
    lesson_goal         TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (lesson_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_lesson_enrollments_user ON lesson_enrollments(user_id);

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

-- ── Section Assets ────────────────────────────────────────────────────────────
-- Typed visual aids attached to a lesson section.
-- asset_type: 'pdf_pages' (range from the lesson PDF) | 'ai_image' (generated image)

CREATE TABLE IF NOT EXISTS section_assets (
    id         TEXT PRIMARY KEY,
    section_id TEXT NOT NULL REFERENCES lesson_sections(id) ON DELETE CASCADE,
    asset_type TEXT NOT NULL,    -- 'pdf_pages' | 'ai_image'
    page_start INTEGER,          -- for pdf_pages: first page (1-based)
    page_end   INTEGER,          -- for pdf_pages: last page (inclusive)
    image_path TEXT,             -- for ai_image: relative path under STORAGE_DIR
    caption    TEXT,
    idx        INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (section_id, idx)
);

-- ── Enrollment Assets ─────────────────────────────────────────────────────────
-- User-generated visual aids produced during a teaching session.
-- Keyed by enrollment (not lesson_sections) so they are per-user state,
-- not shared lesson content.  section_idx mirrors curriculum.idx at generation
-- time; tool_use_id ties the asset to the specific LLM tool call.

CREATE TABLE IF NOT EXISTS enrollment_assets (
    id            TEXT PRIMARY KEY,
    enrollment_id TEXT NOT NULL REFERENCES lesson_enrollments(id) ON DELETE CASCADE,
    section_idx   INTEGER NOT NULL,
    asset_type    TEXT NOT NULL DEFAULT 'ai_image',  -- 'ai_image'
    image_path    TEXT,             -- relative path under STORAGE_DIR
    prompt        TEXT,             -- original user-visible prompt
    revised_prompt TEXT,            -- prompt returned by the image API
    tool_use_id   TEXT,             -- LLM tool_use block id; for history tie-back
    idx           INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_enrollment_assets_enrollment
    ON enrollment_assets(enrollment_id, section_idx);

-- ── Conversation Messages ─────────────────────────────────────────────────────
-- Keyed by enrollment so each user has their own message history.
-- content is JSON-serialised to match Anthropic SDK message format.

CREATE TABLE IF NOT EXISTS messages (
    id            TEXT PRIMARY KEY,
    enrollment_id TEXT NOT NULL REFERENCES lesson_enrollments(id) ON DELETE CASCADE,
    idx           INTEGER NOT NULL,
    role          TEXT NOT NULL,    -- 'user' | 'assistant' | 'tool' | 'tool_result'
    content       TEXT NOT NULL,    -- JSON string
    UNIQUE (enrollment_id, idx)
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
