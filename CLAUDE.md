# pdf_to_audio — Project Briefing

## Architecture
Three-layer topology:
- **React client** — port 5173 (dev) / `frontend/static/` (prod)
- **Frontend BFF** — FastAPI, `0.0.0.0:8000` (`frontend/`)
- **Backend** — FastAPI, `127.0.0.1:8001` (`backend/`)

WebSocket flow: client → BFF (`frontend/routers/ws_proxy.py`) → backend (`backend/routers/ws_session.py`) → STT → agent → TTS

## Key Files
- `backend/routers/ws_session.py` — main WS handler
- `backend/services/agents/session.py` — BackendAgentSession, provider chain construction
- `backend/services/agents/teacher_agent.py` — TeachingAgent (LLM + TTS + curriculum)
- `backend/services/agents/providers/fallback.py` — FallbackLLMProvider
- `backend/services/agents/tts_pipeline.py` — TTSPipeline with ordered provider fallback
- `client/src/pages/TeachPage.tsx` — main teaching UI
- `client/src/lib/types.ts` — WS event types (ClientEvent / ServerEvent)
- `client/src/lib/audio/` — recorder.ts, player.ts, vad.worklet.ts

## Commands
```bash
# Python (uv is NOT on $PATH — always use absolute path)
/home/richard/.local/bin/uv run pytest tests/ -q
/home/richard/.local/bin/uv run pytest tests/backend/ -v
/home/richard/.local/bin/uv sync --dev

# Node (system node is v18, too old for Vite 6 — always activate nvm first)
source /home/richard/.nvm/nvm.sh && nvm use 20
cd client && npm run dev        # dev server, proxies /api and /ws → :8000
npm run build                   # outputs to frontend/static/
```

## Known Gotchas

### Anthropic SDK
- `stream.get_final_message().content` returns Pydantic objects, NOT dicts
- Always convert via `_block_to_api_dict()` before appending to messages
- `model_dump()` includes `parsed_output` → causes 400 on next API call
- `thinking={"type": "adaptive"}` only works on Sonnet/Opus — gate with `if "haiku" not in model`

### FastAPI
- String-quoted annotations with `from __future__ import annotations` are NOT resolved as `Depends`
- Use unquoted annotations or the `Conn` type alias
- `<img src=...>` browser requests send no custom headers → `X-Session-Id` must be optional on page image routes

### WebSocket
- WS frame size is 4 MB (`max_size=4*1024*1024` in `ws_proxy.py`)
- Kokoro audio chunks can exceed 1 MB → split into 65536-sample sub-chunks in `_on_audio_chunk`
- WS connects before PDF decomposition finishes → `curriculum.sections` is empty at connect time
  - Fix: intercept `decompose_complete` in `_send_loop`, update `state.curriculum` in-place

### Provider Fallback
- TTS: `TTSPipeline` takes `providers: list` — on failure, `provider_idx` advances permanently for the turn
- LLM: `FallbackLLMProvider` wraps `[(provider, model), ...]` pairs, tries each in sequence
- Chain construction is in `session.py` — to add a third provider, append to the chain there
- Tests use `tts_providers=[...]` list (not the old `tts_provider`/`fallback_tts_provider` kwargs)

### Curriculum / Enrollment Model
- Lessons split into templates + enrollments after v2 migration
- Published courses are readable by all users; authoring endpoints are creator/admin only
- Auto-start for lessons with existing sections but no messages: triggered directly in `ws_session.py`, not via queue

## Reference Docs
Model optimization (evals, fine-tuning, distillation): `.notes/model_optimization/`
Use `/evals` or `/fine-tune` skills to load relevant context when working in this area.

## Test Credentials
- Non-admin login: `test@mail.com` / `aaaaaaaa`

## Test Status
- 166 passed, 1 skipped (as of 2026-03-18)
- Auth tests in `tests/frontend/test_auth.py` fail due to pre-existing `/api` prefix mismatch — known, not regression

## Deployment
- Stage: dev/portfolio (single VM, personal use)
- Stack: systemd (two units) + nginx TLS + certbot
- Pre-deploy blockers remaining: G9 (password reset), G5 (session expiry), G10 (shared secret)
