# Tests

Three test tiers targeting different layers of the stack. All tests require a PostgreSQL test database.

## Running

```bash
# All tests
/home/appuser/.local/bin/uv run pytest tests/ -q

# Specific tier
/home/appuser/.local/bin/uv run pytest tests/backend/ -v
/home/appuser/.local/bin/uv run pytest tests/frontend/ -v
/home/appuser/.local/bin/uv run pytest tests/integration/ -v

# Single file
/home/appuser/.local/bin/uv run pytest tests/backend/test_task_checklist.py -v
```

## Structure

```
tests/
  conftest.py                   Shared fixtures (DB connection, schema setup, app client)

  backend/                      Unit tests — no HTTP, no WS
    test_task_checklist.py      Task checklist (curriculum, prompt, serialization)
    test_teaching_agent_tts.py  TeacherAgent + TTS pipeline behavior
    test_auth_api.py            Auth endpoint logic
    test_auth_models.py         User model queries
    test_db_lessons.py          Lesson/enrollment DB queries
    test_db_personas.py         Persona DB queries
    test_db_sessions.py         Session DB queries
    test_stt_service.py         STT service
    test_tts_service.py         TTS service
    test_realtime_service.py    Realtime voice service
    test_course_authoring_*.py  Course authoring (sections, OCR)
    test_course_publish_service.py  Course publishing logic
    test_usage_tracker_tts_cost.py  TTS cost tracking
    test_ws_realtime_fallback.py    WS realtime provider fallback
    test_agent_session_ws_close.py  Agent session cleanup on WS close

  frontend/                     BFF tests
    test_auth.py                Auth routes (login, register, verify)
    test_admin_iam.py           Admin IAM routes
    test_ws_proxy.py            WebSocket proxy behavior
    test_session_store.py       Session store logic
    test_rate_limiter.py        Rate limiter middleware

  integration/                  Full-stack tests (BFF + backend)
    conftest.py                 Integration-specific fixtures
    test_ws_session.py          WebSocket session lifecycle
    test_ws_security.py         WS authentication and authorization
    test_tool_invocations.py    Agent tool call round-trips
    test_lessons_api.py         Lesson CRUD API
    test_course_authoring_phase2_api.py  Course authoring API
    test_course_publish_api.py  Course publishing API
    test_course_delete_api.py   Course deletion API
    test_textbook_authoring_api.py  Textbook authoring API
    test_admin_iam_api.py       Admin IAM API
```

## Test Database

Tests use a separate PostgreSQL database (`pdf_to_audio_test` by default). The `conftest.py` fixture:

1. Drops and recreates the `public` schema once per session
2. Applies `backend/db/schema.sql`
3. Wraps each test in a rolled-back transaction for isolation

Set `TEST_DATABASE_URL` to override the connection string.

## Known Issues

- Auth tests in `tests/frontend/test_auth.py` fail due to a pre-existing `/api` prefix mismatch — known, not a regression
- Tests require PostgreSQL running locally; pure unit tests (like `test_task_checklist.py`) still hit the session-scoped schema fixture
