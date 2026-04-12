-- pdf-to-audio database schema
-- PostgreSQL dialect.
-- All primary keys are UUID strings (TEXT).
-- Booleans are SMALLINT (0/1) to keep application code unchanged.
-- Timestamps are TIMESTAMPTZ (UTC).
-- Tables are ordered to satisfy FK constraints.

-- ── Users ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id                      TEXT PRIMARY KEY,
    email                   TEXT UNIQUE,
    display_name            TEXT,
    username                TEXT UNIQUE,
    password_hash           TEXT,
    email_verified          SMALLINT NOT NULL DEFAULT 0,
    is_admin                SMALLINT NOT NULL DEFAULT 0,
    terms_version_accepted  TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Email Verifications ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS email_verifications (
    token      TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Password Reset Tokens ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token      TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Sessions ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Upload Tokens ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS upload_tokens (
    token      TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL
);

-- ── Teaching Personas ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS personas (
    id                 TEXT PRIMARY KEY,
    user_id            TEXT REFERENCES users(id) ON DELETE CASCADE,
    name               TEXT NOT NULL,
    instructions       TEXT NOT NULL,
    voice_instructions TEXT NOT NULL DEFAULT '',
    tts_voice          TEXT NOT NULL DEFAULT '',
    tts_speed          REAL NOT NULL DEFAULT 1.0,
    tts_format         TEXT NOT NULL DEFAULT '',
    tts_prep_prompt    TEXT NOT NULL DEFAULT '',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Courses ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS courses (
    id          TEXT PRIMARY KEY,
    creator_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    description TEXT,
    visibility  TEXT NOT NULL DEFAULT 'draft',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Lessons ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS lessons (
    id          TEXT PRIMARY KEY,
    creator_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id   TEXT REFERENCES courses(id) ON DELETE SET NULL,
    title       TEXT NOT NULL,
    description TEXT,
    pdf_path    TEXT,
    visibility  TEXT NOT NULL DEFAULT 'draft',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Lesson Enrollments ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS lesson_enrollments (
    id                  TEXT PRIMARY KEY,
    lesson_id           TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    user_id             TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    current_section_idx INTEGER NOT NULL DEFAULT 0,
    completed           SMALLINT NOT NULL DEFAULT 0,
    lesson_goal         TEXT,
    task_progress       TEXT NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (lesson_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_lesson_enrollments_user ON lesson_enrollments(user_id);

-- ── Lesson Sections ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS lesson_sections (
    id        TEXT PRIMARY KEY,
    lesson_id TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    idx       INTEGER NOT NULL,
    title     TEXT,
    content   TEXT NOT NULL,
    key_concepts TEXT NOT NULL DEFAULT '[]',
    page_start   INTEGER,
    page_end     INTEGER,
    UNIQUE (lesson_id, idx)
);

CREATE INDEX IF NOT EXISTS idx_lesson_sections_fts
    ON lesson_sections USING GIN (to_tsvector('english', content));

-- ── Section Assets ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS section_assets (
    id         TEXT PRIMARY KEY,
    section_id TEXT NOT NULL REFERENCES lesson_sections(id) ON DELETE CASCADE,
    asset_type TEXT NOT NULL,
    page_start INTEGER,
    page_end   INTEGER,
    image_path TEXT,
    caption    TEXT,
    idx        INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (section_id, idx)
);

-- ── Enrollment Assets ─────────────────────────────────────────────────────────

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
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_enrollment_assets_enrollment
    ON enrollment_assets(enrollment_id, section_idx);

-- ── Conversation Messages ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS messages (
    id            TEXT PRIMARY KEY,
    enrollment_id TEXT NOT NULL REFERENCES lesson_enrollments(id) ON DELETE CASCADE,
    idx           INTEGER NOT NULL,
    role          TEXT NOT NULL,
    content       TEXT NOT NULL,
    UNIQUE (enrollment_id, idx)
);

-- ── Textbook Authoring ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS course_source_files (
    course_id   TEXT PRIMARY KEY REFERENCES courses(id) ON DELETE CASCADE,
    creator_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    pdf_hash    TEXT NOT NULL,
    pdf_path    TEXT NOT NULL,
    page_count  INTEGER NOT NULL,
    toc_json    TEXT NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_course_source_files_pdf_hash ON course_source_files(pdf_hash);

CREATE TABLE IF NOT EXISTS course_chapter_drafts (
    id          TEXT PRIMARY KEY,
    course_id   TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    title       TEXT NOT NULL,
    page_start  INTEGER NOT NULL,
    page_end    INTEGER NOT NULL,
    included    SMALLINT NOT NULL DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(course_id, idx)
);
CREATE INDEX IF NOT EXISTS idx_course_chapter_drafts_course ON course_chapter_drafts(course_id, idx);

CREATE TABLE IF NOT EXISTS textbook_toc_cache (
    pdf_hash      TEXT PRIMARY KEY,
    page_count    INTEGER NOT NULL,
    toc_json      TEXT NOT NULL DEFAULT '[]',
    chapters_json TEXT NOT NULL DEFAULT '[]',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_decomposition_cache_lookup
    ON decomposition_cache(pdf_hash, page_start, page_end, objectives_hash, model, prompt_version);

CREATE TABLE IF NOT EXISTS course_chapter_lessons (
    course_id    TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    chapter_id   TEXT NOT NULL REFERENCES course_chapter_drafts(id) ON DELETE CASCADE,
    lesson_id    TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (course_id, chapter_id)
);
CREATE INDEX IF NOT EXISTS idx_course_chapter_lessons_lesson ON course_chapter_lessons(lesson_id);

CREATE TABLE IF NOT EXISTS course_advisor_sessions (
    course_id          TEXT PRIMARY KEY REFERENCES courses(id) ON DELETE CASCADE,
    creator_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    transcript_json    TEXT NOT NULL DEFAULT '[]',
    objectives_prompt  TEXT,
    status             TEXT NOT NULL DEFAULT 'draft',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS course_decomposition_jobs (
    id                TEXT PRIMARY KEY,
    course_id         TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    creator_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status            TEXT NOT NULL DEFAULT 'queued',
    decompose_mode    TEXT NOT NULL DEFAULT 'pdf',
    objectives_prompt TEXT NOT NULL DEFAULT '',
    total_items       INTEGER NOT NULL DEFAULT 0,
    completed_items   INTEGER NOT NULL DEFAULT 0,
    failed_items      INTEGER NOT NULL DEFAULT 0,
    notify_session_id TEXT,
    error             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
    status       TEXT NOT NULL DEFAULT 'queued',
    error        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_course_decomp_items_job ON course_decomposition_job_items(job_id, idx);

-- ── Course publish copies ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS course_publish_copies (
    source_course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    target_user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source_course_id, target_user_id)
);

CREATE TABLE IF NOT EXISTS lesson_publish_copies (
    source_lesson_id TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    target_user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_lesson_id TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (source_lesson_id, target_user_id)
);

-- ── Usage Tracking ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS usage_raw (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts                 DOUBLE PRECISION NOT NULL,
    user_id            TEXT    NOT NULL DEFAULT '',
    event_type         TEXT    NOT NULL,
    call_type          TEXT    NOT NULL DEFAULT '',
    model              TEXT    NOT NULL DEFAULT '',
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd           DOUBLE PRECISION NOT NULL DEFAULT 0,
    stt_model          TEXT    NOT NULL DEFAULT '',
    stt_language       TEXT    NOT NULL DEFAULT '',
    audio_seconds      DOUBLE PRECISION NOT NULL DEFAULT 0,
    transcription_ms   INTEGER NOT NULL DEFAULT 0,
    tts_voice          TEXT    NOT NULL DEFAULT '',
    tts_characters     INTEGER NOT NULL DEFAULT 0,
    tts_audio_seconds  DOUBLE PRECISION NOT NULL DEFAULT 0,
    tts_synthesis_ms   INTEGER NOT NULL DEFAULT 0,
    aggregated         SMALLINT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS usage_raw_ts         ON usage_raw(ts);
CREATE INDEX IF NOT EXISTS usage_raw_aggregated ON usage_raw(aggregated, ts);

CREATE TABLE IF NOT EXISTS usage_minutes (
    minute_ts          BIGINT NOT NULL,
    user_id            TEXT   NOT NULL DEFAULT '',
    event_type         TEXT   NOT NULL,
    call_type          TEXT   NOT NULL DEFAULT '',
    model              TEXT   NOT NULL DEFAULT '',
    stt_model          TEXT   NOT NULL DEFAULT '',
    stt_language       TEXT   NOT NULL DEFAULT '',
    tts_voice          TEXT   NOT NULL DEFAULT '',
    calls              INTEGER NOT NULL DEFAULT 0,
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd           DOUBLE PRECISION NOT NULL DEFAULT 0,
    audio_seconds      DOUBLE PRECISION NOT NULL DEFAULT 0,
    transcription_ms   INTEGER NOT NULL DEFAULT 0,
    tts_characters     INTEGER NOT NULL DEFAULT 0,
    tts_audio_seconds  DOUBLE PRECISION NOT NULL DEFAULT 0,
    tts_synthesis_ms   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (minute_ts, user_id, event_type, call_type, model,
                 stt_model, stt_language, tts_voice)
);
CREATE INDEX IF NOT EXISTS usage_minutes_ts ON usage_minutes(minute_ts);

CREATE TABLE IF NOT EXISTS usage_hours (
    hour_ts            BIGINT NOT NULL,
    user_id            TEXT   NOT NULL DEFAULT '',
    event_type         TEXT   NOT NULL,
    call_type          TEXT   NOT NULL DEFAULT '',
    model              TEXT   NOT NULL DEFAULT '',
    stt_model          TEXT   NOT NULL DEFAULT '',
    stt_language       TEXT   NOT NULL DEFAULT '',
    tts_voice          TEXT   NOT NULL DEFAULT '',
    calls              INTEGER NOT NULL DEFAULT 0,
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd           DOUBLE PRECISION NOT NULL DEFAULT 0,
    audio_seconds      DOUBLE PRECISION NOT NULL DEFAULT 0,
    transcription_ms   INTEGER NOT NULL DEFAULT 0,
    tts_characters     INTEGER NOT NULL DEFAULT 0,
    tts_audio_seconds  DOUBLE PRECISION NOT NULL DEFAULT 0,
    tts_synthesis_ms   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour_ts, user_id, event_type, call_type, model,
                 stt_model, stt_language, tts_voice)
);
CREATE INDEX IF NOT EXISTS usage_hours_ts ON usage_hours(hour_ts);

-- ── Gamification ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS user_points (
    user_id             TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    total_points        INTEGER NOT NULL DEFAULT 0,
    lessons_completed   INTEGER NOT NULL DEFAULT 0,
    sections_advanced   INTEGER NOT NULL DEFAULT 0,
    current_streak      INTEGER NOT NULL DEFAULT 0,
    longest_streak      INTEGER NOT NULL DEFAULT 0,
    last_lesson_date    TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS point_events (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    enrollment_id TEXT REFERENCES lesson_enrollments(id) ON DELETE SET NULL,
    event_type    TEXT NOT NULL,
    points        INTEGER NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_point_events_user ON point_events(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_point_events_enrollment ON point_events(enrollment_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_point_events_lesson_complete
    ON point_events(enrollment_id, event_type)
    WHERE event_type = 'lesson_complete';

-- ── User Preferences ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id    TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    prefs_json TEXT NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Pending Tool Invocations (Plan B: persisted interactive tool waits) ──────
-- One row per enrollment whose teaching loop is suspended on an interactive
-- tool waiting for the student's response.  Survives WS disconnect, server
-- restart, and device-switch.  PRIMARY KEY on enrollment_id enforces "at most
-- one pending tool per enrollment" (the run_turn dispatcher awaits sequentially
-- so this matches reality).
CREATE TABLE IF NOT EXISTS pending_tool_invocations (
    enrollment_id   TEXT PRIMARY KEY REFERENCES lesson_enrollments(id) ON DELETE CASCADE,
    invocation_id   TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    tool_use_id     TEXT NOT NULL,
    turn_id         TEXT NOT NULL,
    event_payload   JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Idempotent migrations ────────────────────────────────────────────────────
ALTER TABLE lesson_enrollments ADD COLUMN IF NOT EXISTS task_progress TEXT NOT NULL DEFAULT '{}';
ALTER TABLE lessons ADD COLUMN IF NOT EXISTS visual_aid_config TEXT NOT NULL DEFAULT '{}';
ALTER TABLE courses ADD COLUMN IF NOT EXISTS visual_aid_config TEXT NOT NULL DEFAULT '{}';
ALTER TABLE courses ADD COLUMN IF NOT EXISTS default_persona_id TEXT REFERENCES personas(id) ON DELETE SET NULL;
ALTER TABLE lesson_enrollments ADD COLUMN IF NOT EXISTS persona_id TEXT REFERENCES personas(id) ON DELETE SET NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS terms_version_accepted TEXT;
