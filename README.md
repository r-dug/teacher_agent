# teacher_agent

A voice-first AI teaching platform that turns any PDF into an interactive, spoken lesson. Students learn through conversation — the agent explains concepts, asks questions, runs exercises, and tracks mastery before advancing.

## Architecture

Three-layer topology:

```
React Client (:5173 dev / static prod)
      |
  WebSocket + REST
      |
Frontend BFF (:8000)          ← auth, rate-limiting, WS proxy
      |
  Internal proxy
      |
Backend (:8001, loopback)     ← agents, voice, DB, curriculum
```

**WebSocket flow:** client &rarr; BFF (`frontend/routers/ws_proxy.py`) &rarr; backend (`backend/routers/ws_session.py`) &rarr; STT &rarr; agent &rarr; TTS &rarr; client

## Project Layout

```
backend/              FastAPI backend — agents, voice pipeline, database
  db/                 PostgreSQL schema, migrations, query helpers
  routers/            API + WebSocket endpoints
  services/
    agents/           LLM teaching agent, provider chain, tools, prompts
    voice/            STT, TTS, realtime, phonetics
    documents/        PDF processing, course authoring
    images/           AI image generation, web image search
client/               React + TypeScript frontend (Vite)
  src/pages/          Page components (TeachPage, LoginPage, etc.)
  src/components/     Reusable UI (Sketchpad, CodeEditor, SlideViewer, etc.)
  src/lib/            WebSocket client, audio recorder/player, types
frontend/             FastAPI BFF — auth, session management, WS proxy
  routers/            BFF routes (auth, courses, lessons, ws_proxy)
  static/             Production build output from client/
tests/
  backend/            Unit tests (agents, DB, voice, auth)
  frontend/           BFF tests (proxy, sessions, rate limiter)
  integration/        Full-stack tests (WS sessions, tool invocations, security)
.notes/               Research docs (model optimization, cost analysis, courses)
scripts/              Deployment and provisioning scripts
```

## Quick Start

### Prerequisites

- Python 3.12+ with [uv](https://docs.astral.sh/uv/)
- Node.js 20+ (via nvm)
- PostgreSQL

### Backend

```bash
# Install dependencies
/home/appuser/.local/bin/uv sync --dev

# Run backend (loopback only, port 8001)
/home/appuser/.local/bin/uv run python -m backend.main

# Run BFF (port 8000)
/home/appuser/.local/bin/uv run python -m frontend.main
```

### Client

```bash
source /home/appuser/.nvm/nvm.sh && nvm use 20
cd client
npm install
npm run dev          # dev server on :5173, proxies to :8000
npm run build        # production build to frontend/static/
```

### Tests

```bash
/home/appuser/.local/bin/uv run pytest tests/ -q
```

## Key Features

- **Voice conversation** — STT + LLM + TTS pipeline with provider fallback
- **PDF curriculum** — uploads decomposed into structured lessons with sections and key concepts
- **Interactive tools** — sketchpad, code editor, HTML editor, camera, timer, AI image generation
- **Per-concept mastery tracking** — structured task checklist per section, persisted to DB
- **Course authoring** — create, publish, and share multi-lesson courses
- **Gamification** — points, streaks, leaderboard

## Deployment

Single-VM stack: systemd (two units) + nginx TLS + certbot. See `scripts/deploy.sh`.
