# Backend

FastAPI server running on `127.0.0.1:8001` (loopback only — not exposed to the network). All external traffic reaches it through the BFF proxy on `:8000`.

## Structure

```
backend/
  main.py                 Entry point, startup/shutdown lifecycle
  config.py               Settings (env vars, API keys, model defaults)
  app_state.py            Singleton holding loaded models and trackers
  auth.py                 JWT token verification (shared with BFF)
  logging_config.py       Structured logging setup

  db/                     PostgreSQL schema, migrations, query helpers
  routers/                API + WebSocket endpoints
  services/
    agents/               LLM teaching agent, provider chain, tools, prompts
    voice/                STT, TTS, realtime voice, phonetics
    documents/            PDF processing, course and textbook authoring
    images/               AI image generation (OpenAI DALL-E), web image search
```

## Key Modules

### `routers/ws_session.py`

The main WebSocket handler. Manages the full lifecycle of a teaching session:

1. Authenticates the connection, loads or creates the enrollment
2. Restores curriculum, messages, and task progress from DB
3. Runs the agent loop (`_run_turn`) in a background thread
4. Persists state after each turn via `_save_state`
5. Handles reconnection (replays history, resends pending tool events)

### `services/agents/`

The agent subsystem. See [agents/README.md](services/agents/README.md) for details.

### `services/voice/`

STT and TTS pipeline with provider fallback. See [voice/README.md](services/voice/README.md) for details.

### `routers/`

| File | Endpoints |
|------|-----------|
| `ws_session.py` | `WS /ws/session` — main teaching WebSocket |
| `courses.py` | `/courses/*` — course CRUD, chapter management |
| `lessons.py` | `/lessons/*` — lesson CRUD, PDF upload, enrollment |
| `voice/` | `/voices` — available TTS voices |
| `agents/` | `/personas` — agent persona management |
| `iam.py` | `/iam/*` — admin user management |
| `usage.py` | `/usage/*` — token and cost tracking |
| `leaderboard.py` | `/leaderboard` — gamification rankings |
| `preferences.py` | `/preferences` — user settings |

## Running

```bash
# Standalone
/home/appuser/.local/bin/uv run python -m backend.main

# With auto-reload (dev)
/home/appuser/.local/bin/uv run uvicorn backend.main:app --host 127.0.0.1 --port 8001 --reload
```

## Configuration

All settings are in `config.py`, loaded from environment variables. Key ones:

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Anthropic API (primary LLM provider) |
| `OPENAI_API_KEY` | OpenAI API (fallback LLM, TTS, image generation) |
| `DATABASE_URL` | PostgreSQL connection string |
| `STORAGE_DIR` | File storage path (PDFs, images) |
| `LLM_MODEL` | Default teaching model (e.g. `claude-sonnet-4-20250514`) |
| `TTS_PROVIDER` | Primary TTS (`openai`, `kokoro`) |
