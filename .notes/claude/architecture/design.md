# Client-Server Architecture Design

_Status: Phases 1–3 complete. Phase 4 (integration + hardening) next._

---

## Decisions Summary

| Question | Decision |
|----------|----------|
| Frontend server role | BFF proxy (auth, rate-limiting, session; no business logic) |
| PDF upload path | Client → Backend directly (frontend issues a short-lived upload token) |
| User schema | `user_id` FK from day one, even before auth |
| Ports | Frontend: 8000 (LAN), Backend: 8001 (loopback only) |
| STT / TTS location | **Server-side** (backend) |
| Streaming protocol | **WebSocket** (bidirectional audio makes SSE non-viable) |
| Sketchpad async | invocation_id + asyncio.Event on backend |
| Backend process split | Single process, separate routers; splitting later is a deploy change |
| Deployment target | Prototype: single machine (backend + client co-located). Kokoro is local. |
| Audio proxy latency | Acceptable with VAD (utterance-level chunks); see note below |
| Client technology | **React + TypeScript** (Vite). Tkinter apps kept as-is, not migrated. |
| Client scope (Phase 3) | Teaching app only (`teach.py` equivalent). `record_stt.py` out of scope. |
| UI / component library | **shadcn/ui + Tailwind CSS**. Design tokens centralised in `tailwind.config.ts`. Aesthetic specifics deferred. |
| Wake word (web) | Not available in browser — button-to-talk only for web client. |

---

## Rationale: BFF Proxy

A pure pass-through might seem "simpler," but the BFF layer serves security and
portability goals directly:

- Backend is **never reachable from the network** (bound to loopback only).
- Auth, rate limiting, and session validation happen at the BFF — the backend can
  trust all traffic it receives.
- Different clients (Tkinter, React, mobile) connect to the same BFF; the backend
  API is stable regardless of client evolution.
- The BFF is the right place for eventual API versioning and per-client response shaping.

The BFF owns: session tokens, rate limiting, auth enforcement (stubbed for now), and
proxying. It does not own business logic.

## Rationale: SSE vs WebSocket

With STT on the server, the client must send audio to the server. SSE is
server→client only, so WebSocket is the only viable choice.

## Note: WS Proxy Audio Latency

The feedback raised a valid concern about audio being relayed through the frontend
proxy. With our **VAD-first design**, the client sends complete utterances
(silence-delimited chunks), not a continuous audio stream. Each transmission is a
single discrete blob (~10–100 KB), sent over loopback. The additional hop is
microseconds, not meaningful latency.

This design decision would need revisiting if we move to continuous audio streaming
(e.g., streaming ASR mid-utterance). At that point, consider: frontend issues a
signed token → client connects directly to backend WS for audio only, keeping
control messages through the BFF.

---

## Topology

```
╔══════════════════════════╗
║  Client                  ║
║  (Tkinter → React/Mobile)║
║  - Records audio (sd)    ║
║  - VAD / silence detect  ║
║  - Wake word detect      ║    ← lightweight, stays client-side
║  - Plays received audio  ║
║  - Renders text/slides   ║
║  - Draws on sketchpad    ║
║  - Buffers audio_turns   ║    ← last 10 turns, LRU eviction
╚══════════╤═══════════════╝
           │  WS ws://host:8000/ws/{session_id}
           │  REST http://host:8000/
╔══════════▼═══════════════╗
║  Frontend Server         ║
║  FastAPI / uvicorn       ║
║  0.0.0.0:8000            ║
║  - Session tokens        ║
║  - Auth enforcement      ║    ← stub now, JWT later
║  - Rate limiting         ║    ← token bucket per session
║  - WS proxy              ║
║  - REST proxy            ║
║  - Reconnection state    ║    ← tracks last turn_id per session
╚══════════╤═══════════════╝
           │  WS ws://127.0.0.1:8001/internal/ws/{session_id}
           │  REST http://127.0.0.1:8001/
╔══════════▼═══════════════╗
║  Backend Server          ║
║  FastAPI / uvicorn       ║
║  127.0.0.1:8001          ║
║  - STT (faster-whisper)  ║
║  - TTS (Kokoro — local)  ║    ← requires backend on same machine as models
║  - AI agent (Claude API) ║
║  - Lesson CRUD           ║
║  - SQLite (→ Postgres)   ║
║  - File storage (local)  ║    ← STORAGE_DIR configurable; S3-compatible later
╚══════════════════════════╝
```

**Deployment constraint (prototype):** Backend and Kokoro model run on the same
machine. Remote deployment requires either shipping Kokoro to a GPU server or
substituting a cloud TTS API (e.g., ElevenLabs, Google TTS). This is a known
transition point, not an architectural blocker.

---

## Audio Flow

### STT (user speech → text)

1. Client records audio with sounddevice (16 kHz, float32, mono).
2. Client runs VAD locally — cheap, deterministic, reduces network traffic.
3. When an utterance ends, client sends a single WebSocket message:
   `{"event": "audio_input", "data": "<b64 PCM float32>", "sample_rate": 16000}`
4. Frontend Server validates session, forwards to Backend.
5. Backend runs faster-whisper, replies:
   `{"event": "transcription", "text": "...", "turn_id": "uuid"}`
6. Frontend Server relays to Client.

### TTS (text → audio)

1. Backend LLM streams text; each chunk is sent as:
   `{"event": "text_chunk", "text": "...", "turn_idx": N}`
2. Kokoro synthesizes in chunks. Each chunk is sent as:
   `{"event": "audio_chunk", "data": "<b64 float32>", "sample_rate": 24000,
     "turn_idx": N, "chunk_idx": M}`
   followed by:
   `{"event": "chunk_complete", "turn_idx": N, "chunk_idx": M}`
3. Client decodes and plays audio chunks progressively.
4. Client buffers received audio in `audio_turns[turn_idx][chunk_idx]` for click-to-play.
   **Eviction policy**: retain last 10 turns; oldest is dropped when turn 11 arrives.

**Audio encoding**: base64 float32 PCM for prototype simplicity.
Optimisation path: binary WS frames with a 12-byte header (4B turn_idx, 4B chunk_idx,
4B n_samples) reduces bandwidth ~25% and eliminates encode/decode overhead.

---

## File Storage

PDF files uploaded by the client are stored by the backend.

- **Prototype**: local filesystem at `STORAGE_DIR` (env var, defaults to `./storage/`).
  Subdirectory per user: `STORAGE_DIR/{user_id}/pdfs/{lesson_id}.pdf`.
- **DB column**: `pdf_path` stores a relative key (e.g. `{user_id}/pdfs/{lesson_id}.pdf`),
  not an absolute path. The backend resolves against `STORAGE_DIR` at runtime.
- **Production path**: swap `STORAGE_DIR` resolution for an S3-compatible client
  (e.g. `boto3`) without changing the DB schema.

The client obtains a short-lived upload token from the frontend server before
uploading directly to the backend, keeping the backend hidden from routine traffic.

---

## TeachingAgent → Async Boundary (Implementation Note)

`TeachingAgent.run_turn()` is synchronous and uses `threading.Event` and `queue.Queue`
internally. The backend WS handler is async. The boundary is managed as follows:

1. The WS handler calls `await asyncio.to_thread(agent.run_turn, ...)`, which runs
   the entire synchronous agent turn in a thread pool worker. This does not block
   the event loop.

2. The sketchpad callback (`on_open_sketchpad`) is called from the worker thread.
   It must: (a) send a WS message to the client, and (b) block until the client
   responds. Implementation:

   ```python
   # In the async WS handler setup:
   loop = asyncio.get_event_loop()
   done = threading.Event()
   result_holder = []

   def on_sketchpad(prompt, result_holder, done_event):
       # Schedule the WS send on the event loop from the worker thread
       asyncio.run_coroutine_threadsafe(
           ws.send_json({"event": "open_sketchpad",
                         "prompt": prompt,
                         "invocation_id": inv_id}),
           loop
       ).result()           # blocks until send completes
       done_event.wait()    # blocks until client POSTs tool_result

   # In the async tool_result handler:
   async def handle_tool_result(inv_id, data):
       result_holder.append(data)
       done.set()           # unblocks the worker thread
   ```

   This pattern is structurally identical to the existing Tkinter pattern
   (`root.after` to dispatch to main thread, then `event.wait()`).

3. All other callbacks (text chunks, audio chunks, section advance) are called from
   the worker thread and use `asyncio.run_coroutine_threadsafe` to schedule WS sends.

This is non-trivial but contained to `backend/routers/ws_session.py`. No changes to
`shared/teaching_agent.py` are required.

---

## Crash Recovery and Reconnection

- Each agent turn is assigned a `turn_id` (UUID) at start.
- Frontend Server maintains per-session state: `{session_id: {last_turn_id, turn_status}}`.
  `turn_status` ∈ `{pending, complete, failed}`.
- On WS reconnect, client sends `{"event": "reconnect", "last_turn_id": "..."}`.
- Frontend Server checks state:
  - `complete` → no action, client re-renders from lesson state.
  - `pending` → send `{"event": "turn_interrupted"}` so client can prompt user to retry.
  - `failed` or unknown → same as pending.
- Backend does not persist in-flight turn state in the prototype; interrupted turns
  are simply retried.

---

## WebSocket Message Protocol

All messages are JSON. Event names are snake_case strings.

### Client → Frontend Server

```jsonc
// Utterance audio (VAD-segmented, one blob per utterance)
{"event": "audio_input", "data": "<b64 PCM float32>", "sample_rate": 16000}

// Sketchpad result
{"event": "tool_result", "invocation_id": "uuid", "result": {"drawing": "<b64 PNG>"}}

// Reconnect after drop
{"event": "reconnect", "last_turn_id": "uuid"}

// Cancel in-progress agent turn
{"event": "cancel_turn"}
```

### Frontend Server → Client

```jsonc
{"event": "transcription",     "text": "...", "turn_id": "uuid"}
{"event": "text_chunk",        "text": "...", "turn_idx": 0}
{"event": "audio_chunk",       "data": "<b64>", "sample_rate": 24000,
                                "turn_idx": 0, "chunk_idx": 0}
{"event": "chunk_complete",    "turn_idx": 0, "chunk_idx": 0}
{"event": "turn_complete",     "turn_id": "uuid"}
{"event": "turn_interrupted"}
{"event": "show_slide",        "page": 3, "caption": "..."}
{"event": "open_sketchpad",    "prompt": "...", "invocation_id": "uuid"}
{"event": "section_advanced",  "curriculum": {"title": "...", "idx": 2, "total": 5}}
{"event": "curriculum_complete"}
{"event": "tts_playing",       "playing": true}
{"event": "status",            "message": "Thinking..."}
{"event": "error",             "message": "..."}
```

Frontend Server ↔ Backend uses the same protocol over an internal WS connection.

---

## REST Endpoints

### Frontend Server (port 8000, LAN)

```
POST   /sessions                     → create session; return {session_id}
DELETE /sessions/{session_id}        → end session

GET    /lessons                      → list lessons (session-scoped)
GET    /lessons/{lesson_id}          → lesson metadata
DELETE /lessons/{lesson_id}

GET    /sessions/{session_id}/upload_token → short-lived token for direct PDF upload
                                             (session-scoped; lesson created by backend
                                              POST /lessons/decompose, which returns lesson_id)

GET    /personas
GET    /voices
```

### Backend (port 8001, loopback)

```
POST   /lessons/decompose            → multipart PDF upload (token in header)
                                       returns {lesson_id}; decomposition is async,
                                       progress streamed over existing WS
GET    /lessons/{lesson_id}          → full lesson (sections + messages)
POST   /lessons/{lesson_id}          → upsert lesson state
DELETE /lessons/{lesson_id}

GET    /personas
POST   /personas
DELETE /personas/{persona_id}

GET    /voices
```

WebSocket:
```
WS  /ws/{session_id}                 → main session channel (both servers)
WS  /internal/ws/{session_id}        → frontend→backend relay
```

---

## shared/ Usage in Thin Client

With server-side STT and TTS, the thin client needs almost nothing from `shared/`.

| Module | Client needs? | Notes |
|--------|--------------|-------|
| `shared/audio.py` | **Yes** (WakeWordDetector only) | Wake word stays client-side; openwakeword is lightweight |
| `shared/constants.py` | Maybe | Only if client needs voice/model names for UI dropdowns |
| `shared/stt.py` | **No** | Moved to backend |
| `shared/teaching_agent.py` | **No** | Backend only |
| `shared/lesson.py` | **No** | Backend / DB only |
| `shared/voice_pipeline.py` | **Partial** | VAD logic extracted to `client/audio.py`; the rest drops off |
| `shared/ui.py` | Maybe | `StderrInterceptor` — only if still loading models locally |

The client will have its own `client/audio.py` for sounddevice + VAD + playback,
extracting only what it needs from `shared/voice_pipeline.py`.

---

## Database Schema (SQLite via aiosqlite)

Note: aiosqlite serializes all writes through a single connection. This is not a
connection pool — it is a single async-wrapped connection. SQLite's single-writer
constraint means concurrent write-heavy workloads will queue. Acceptable for a
single-user prototype; migrate to PostgreSQL + asyncpg for multi-user production.

```sql
-- Users (stub: one anonymous user seeded at startup for prototype)
CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,          -- UUID
    email        TEXT UNIQUE,               -- NULL in prototype
    display_name TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

-- Sessions
CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,            -- UUID; also the opaque bearer token
    user_id    TEXT NOT NULL REFERENCES users(id),
    created_at TEXT DEFAULT (datetime('now')),
    last_seen  TEXT DEFAULT (datetime('now'))
);

-- Lessons
CREATE TABLE IF NOT EXISTS lessons (
    id                  TEXT PRIMARY KEY,   -- UUID
    user_id             TEXT NOT NULL REFERENCES users(id),
    title               TEXT NOT NULL,
    pdf_path            TEXT,               -- relative key under STORAGE_DIR
    current_section_idx INTEGER NOT NULL DEFAULT 0,
    completed           INTEGER NOT NULL DEFAULT 0,  -- boolean (0/1)
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
);

-- Lesson Sections
CREATE TABLE IF NOT EXISTS lesson_sections (
    id        TEXT PRIMARY KEY,             -- UUID
    lesson_id TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    idx       INTEGER NOT NULL,
    title     TEXT,
    content   TEXT NOT NULL,
    UNIQUE (lesson_id, idx)
);

-- Conversation Messages
CREATE TABLE IF NOT EXISTS messages (
    id        TEXT PRIMARY KEY,             -- UUID
    lesson_id TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    idx       INTEGER NOT NULL,
    role      TEXT NOT NULL,               -- 'user' | 'assistant' | 'tool' | 'tool_result'
    content   TEXT NOT NULL,               -- JSON-serialised (Anthropic SDK format)
    UNIQUE (lesson_id, idx)
);

-- Teaching Personas
CREATE TABLE IF NOT EXISTS personas (
    id           TEXT PRIMARY KEY,          -- slug, e.g. 'socratic'
    user_id      TEXT REFERENCES users(id), -- NULL = built-in / global
    name         TEXT NOT NULL,
    instructions TEXT NOT NULL,
    created_at   TEXT DEFAULT (datetime('now'))
);
```

---

## Project Structure

```
pdf_to_audio/
├── client/
│   ├── __init__.py
│   ├── audio.py              # sounddevice recording, VAD, playback, wake word
│   ├── ws_client.py          # reconnecting WS helper
│   ├── teach_client.py       # Tkinter teaching app (thin: WS events + display)
│   └── voice_client.py       # Tkinter voice assistant (thin)
├── frontend/
│   ├── __init__.py
│   ├── main.py               # FastAPI app + uvicorn entrypoint
│   ├── session_store.py      # in-memory {session_id: {user_id, last_turn_id, status}}
│   ├── rate_limiter.py       # token bucket per session
│   └── routers/
│       ├── sessions.py
│       ├── lessons.py
│       ├── personas.py
│       └── ws_proxy.py       # WS relay: client ↔ backend
├── backend/
│   ├── __init__.py
│   ├── main.py               # FastAPI app + uvicorn entrypoint
│   ├── config.py             # env vars: STORAGE_DIR, ANTHROPIC_API_KEY, etc.
│   ├── db/
│   │   ├── schema.sql
│   │   ├── connection.py     # single aiosqlite connection (not a pool)
│   │   └── models.py         # typed async query helpers (no ORM)
│   ├── services/
│   │   ├── stt.py            # async wrapper around shared/stt.py
│   │   ├── tts.py            # async generator yielding PCM chunks (Kokoro)
│   │   └── agent.py          # async wrapper around shared/teaching_agent.py
│   └── routers/
│       ├── lessons.py
│       ├── personas.py
│       ├── voices.py
│       └── ws_session.py     # WS handler: STT → agent loop → TTS streaming
├── shared/                   # existing — minimal changes
│   ├── audio.py
│   ├── constants.py
│   ├── lesson.py
│   ├── stt.py
│   ├── teaching_agent.py
│   ├── ui.py
│   └── voice_pipeline.py
└── tests/
    ├── conftest.py           # shared fixtures: test DB, mock WS, ASGI test client
    ├── backend/
    │   ├── test_db_lessons.py
    │   ├── test_db_sessions.py
    │   ├── test_db_personas.py
    │   ├── test_stt_service.py
    │   ├── test_tts_service.py
    │   └── test_agent_service.py
    ├── frontend/
    │   ├── test_session_store.py
    │   ├── test_rate_limiter.py
    │   └── test_ws_proxy.py
    └── integration/
        ├── test_full_turn.py       # audio → transcription → agent → text/audio stream
        ├── test_tool_invocations.py # show_slide, open_sketchpad round-trip
        └── test_session_isolation.py  # two sessions don't cross-contaminate
```

---

## Implementation Phases (revised)

### Phase 1: Backend Server + Integration Test Stubs

1. `backend/config.py` — env var loading
2. `backend/db/`: schema, connection, typed query helpers
3. `backend/routers/lessons.py` — CRUD (replaces `LessonStore` file I/O)
4. `backend/routers/personas.py`
5. `backend/services/stt.py` — async faster-whisper wrapper
6. `backend/services/tts.py` — async Kokoro chunk generator
7. `backend/services/agent.py` — `asyncio.to_thread` wrapper; sketchpad async boundary
8. `backend/routers/ws_session.py` — WS handler wiring all services
9. **Tests** (co-developed, not deferred):
   - `tests/conftest.py` with in-memory DB fixture and mock WS server
   - `tests/backend/test_db_*.py`
   - `tests/backend/test_stt_service.py`, `test_tts_service.py`
   - `tests/integration/test_full_turn.py` stub (mock STT/TTS, real agent protocol)

### Phase 2: Frontend Server

1. `frontend/session_store.py`
2. `frontend/rate_limiter.py`
3. `frontend/routers/sessions.py`, `lessons.py`, `personas.py`
4. `frontend/routers/ws_proxy.py` — relay + reconnect state tracking
5. **Tests**: `tests/frontend/test_*.py`

### Phase 3: Thin Client

1. `client/audio.py` — sounddevice + VAD + wake word + playback; audio_turns LRU (N=10)
2. `client/ws_client.py` — reconnecting WS helper, event dispatch
3. `client/teach_client.py` — Tkinter GUI wired to WS events; no agent/STT/TTS code
4. `client/voice_client.py` — simpler; do after teach_client.py
5. **Tests**: mock WS server fixtures, test event handling and audio buffering

### Phase 4: Integration + Hardening

1. End-to-end integration tests (real servers, test fixtures)
2. Session isolation verification
3. Reconnection / crash recovery testing
4. Performance baseline: measure round-trip latency for a single turn

---

## Security Design

| Concern | Prototype | Production |
|---------|-----------|------------|
| Network exposure | Frontend on LAN (0.0.0.0:8000), backend loopback only | Frontend behind TLS terminator (nginx/caddy) |
| Auth | Opaque session_id as bearer token | JWT + MFA (OTP email/SMS) |
| Transport | ws:// + http:// | wss:// + https:// |
| Rate limiting | Token bucket per session (in-memory) | Redis-backed, per-user |
| Input validation | Pydantic models on all endpoints | Same + WAF |
| CORS | Restricted to localhost | Strict allowlist |
| Secrets | ANTHROPIC_API_KEY in env | Secret manager (Vault / AWS SM) |
| File upload | Token in header, backend validates | Signed upload URL with TTL |

---

## Open Items

- [ ] Audio optimisation: binary WS frames with 12-byte header instead of base64.
      Defer until prototype is working.
- [ ] `voice_client.py` after `teach_client.py` is proven.
- [ ] DBMS migration: swap `aiosqlite` for `asyncpg` + PostgreSQL. Schema is
      already Postgres-compatible.
- [ ] Remote deployment: when backend moves off local machine, swap Kokoro for a
      cloud TTS API or deploy Kokoro behind its own service endpoint.
- [ ] CI: GitHub Actions to run test suite on push.
- [ ] Continuous audio streaming: if we ever want streaming ASR (mid-utterance
      transcription), revisit WS proxy latency and consider direct client→backend
      audio channel with BFF-issued token.
