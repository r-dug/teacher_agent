"""
Backend server entry point.

Starts a FastAPI application on 127.0.0.1:8001 (loopback only).
Models (STT, TTS) are loaded once during startup and stored in app_state
for reuse across requests and WebSocket sessions.

Run with:
    python -m backend.main
or via the project launcher (to be added in Phase 2).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from .app_state import app_state
from .config import settings
from .db import connection as db, models
from .routers import courses, internal, lessons, personas, voices, ws_session, usage


# ── lifespan ───────────────────────────────────────────────────────────────────

async def _usage_background_task() -> None:
    """Aggregate usage_raw → usage_minutes every 60 s; roll months once per day."""
    import calendar
    from datetime import datetime, timezone
    last_roll_month: int | None = None

    while True:
        await asyncio.sleep(60)
        try:
            app_state.token_tracker.aggregate_minutes()
        except Exception as exc:
            print(f"[usage] aggregate_minutes error: {exc}")
        # Roll previous month on first run each day if it's the 1st
        now = datetime.now(timezone.utc)
        if now.day == 1 and last_roll_month != now.month:
            try:
                n = app_state.token_tracker.roll_month_to_hours()
                if n:
                    print(f"[usage] rolled {n} minute rows to hours.")
            except Exception as exc:
                print(f"[usage] roll_month_to_hours error: {exc}")
            last_roll_month = now.month


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure storage dirs exist
    settings.ensure_dirs()

    # Initialise database
    await db.init(settings.DB_PATH)
    conn = await db.get().__anext__()
    await models.seed_personas(conn)
    await models.seed_admin_users(conn)

    # Initialise usage tracker with its own sync SQLite connection
    app_state.token_tracker.init(settings.DB_PATH)
    # Roll previous month if we're on the 1st
    from datetime import datetime, timezone
    if datetime.now(timezone.utc).day == 1:
        app_state.token_tracker.roll_month_to_hours()

    # Load ML models in thread pool (blocking operations)
    # STT model is loaded lazily on first transcription request (avoids blocking startup)

    print(f"Loading Kokoro TTS ({settings.DEFAULT_VOICE})...")
    from .services.tts import build_tts_providers, load_kokoro_pipeline

    app_state.kokoro_pipeline = await asyncio.to_thread(
        load_kokoro_pipeline, settings.DEFAULT_VOICE
    )
    selected_provider = settings.effective_tts_provider()
    app_state.tts_provider, app_state.tts_fallback_provider = build_tts_providers(
        selected_provider=selected_provider,
        kokoro_pipeline=app_state.kokoro_pipeline,
        default_kokoro_voice=settings.DEFAULT_VOICE,
        openai_api_key=settings.OPENAI_API_KEY,
        openai_model=settings.OPENAI_TTS_MODEL,
        openai_voice=settings.OPENAI_TTS_VOICE,
        openai_format=settings.OPENAI_TTS_FORMAT,
        openai_timeout_seconds=settings.OPENAI_TTS_TIMEOUT_S,
        openai_max_retries=settings.OPENAI_TTS_MAX_RETRIES,
        openai_cost_per_minute_usd=settings.OPENAI_TTS_COST_PER_MINUTE_USD,
    )
    app_state.active_tts_provider = selected_provider
    if selected_provider == "openai":
        print("TTS ready. Primary=openai, fallback=kokoro.")
    else:
        print("TTS ready. Primary=kokoro.")

    teach_provider = (settings.TEACH_LLM_PROVIDER or "anthropic").strip().lower()
    teach_model = settings.TEACH_LLM_MODEL or settings.LLM_MODEL
    print(f"Teach LLM: provider={teach_provider}, model={teach_model}")
    print(
        "Decompose LLM: "
        f"provider={settings.effective_decompose_llm_provider()}, "
        f"model={settings.effective_decompose_llm_model()}"
    )

    # Background usage aggregation
    bg_task = asyncio.create_task(_usage_background_task())

    yield

    # Shutdown
    bg_task.cancel()
    app_state.token_tracker.close()
    await db.close()


# ── app ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="pdf-to-audio Backend",
    version="0.1.0",
    lifespan=lifespan,
    # Only accessible from the frontend server; no public docs in production.
    docs_url="/docs",
    redoc_url=None,
)

class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


class _InternalTokenMiddleware(BaseHTTPMiddleware):
    """Reject requests that don't carry the shared BFF→backend secret."""

    async def dispatch(self, request: Request, call_next):
        if settings.BACKEND_SHARED_SECRET and request.url.path != "/health":
            token = request.headers.get("x-internal-token", "")
            if token != settings.BACKEND_SHARED_SECRET:
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=403, content={"detail": "Forbidden"})
        return await call_next(request)


app.add_middleware(_InternalTokenMiddleware)
app.add_middleware(_SecurityHeadersMiddleware)

# Allow the frontend server to call us
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── routers ────────────────────────────────────────────────────────────────────

app.include_router(internal.router)
app.include_router(courses.router)
app.include_router(lessons.router)
app.include_router(personas.router)
app.include_router(voices.router)
app.include_router(ws_session.router)
app.include_router(usage.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── entrypoint ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=False,
        ws_max_size=4 * 1024 * 1024,  # 4 MB — mirrors the frontend proxy limit
    )
