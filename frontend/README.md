# Frontend BFF

FastAPI Backend-for-Frontend running on `0.0.0.0:8000`. This is the only externally-exposed server — it sits between the React client and the backend.

## Responsibilities

1. **Authentication** — issues and validates JWT session tokens, handles login/register/verify flows
2. **Rate limiting** — per-session request throttling
3. **WebSocket proxy** — proxies client WS connections to the backend (`ws_proxy.py`)
4. **REST proxy** — forwards API requests to `127.0.0.1:8001` with auth context
5. **Static file serving** — serves the production React build from `static/`
6. **Upload tokens** — issues short-lived tokens for direct client-to-backend PDF uploads

## Structure

```
frontend/
  main.py               Entry point, middleware stack, static file serving
  config.py             BFF settings (session secret, backend URL, etc.)
  logging_config.py     Structured logging
  http_client.py        Shared httpx client for backend calls
  session_store.py      In-memory session store
  email_service.py      Email sending (verification, password reset)

  routers/
    auth.py             /auth/* — login, register, verify email, reset password
    ws_proxy.py         WS /ws/* — WebSocket proxy to backend
    courses.py          /courses/* — proxied course endpoints
    lessons.py          /lessons/* — proxied lesson endpoints
    personas.py         /personas/* — agent persona management
    voices.py           /voices — TTS voice listing
    iam.py              /iam/* — admin user management
    leaderboard.py      /leaderboard — gamification
    usage.py            /usage/* — analytics
    preferences.py      /preferences — user settings
    sessions.py         /sessions — session management
    admin_guard.py      Middleware for admin-only routes

  static/               Production React build (output of client/npm run build)
```

## WebSocket Proxy

`ws_proxy.py` is the critical path for teaching sessions:

- Client connects to `ws://host:8000/ws/session`
- BFF validates the session token
- Opens a backend WS to `ws://127.0.0.1:8001/ws/session`
- Bidirectionally proxies frames between client and backend
- Frame size limit: 4 MB (`max_size=4*1024*1024`)

## Running

```bash
# Standalone
/home/appuser/.local/bin/uv run python -m frontend.main

# With auto-reload (dev)
/home/appuser/.local/bin/uv run uvicorn frontend.main:app --host 0.0.0.0 --port 8000 --reload
```

## Production

In production, nginx terminates TLS and forwards to `:8000`. The BFF serves `static/index.html` for all non-API routes (SPA routing). Managed by systemd.
