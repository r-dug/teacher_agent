"""
Backend server entry point.

Starts a FastAPI application on 127.0.0.1:8001 (loopback only).
Models (STT, Kokoro TTS) are loaded once during startup and stored in
app_state for reuse across requests and WebSocket sessions.

Run with:
    python -m backend.main
or via the project launcher (to be added in Phase 2).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .app_state import app_state
from .config import settings
from .db import connection as db, models
from .routers import internal, lessons, personas, voices, ws_session


# ── lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure storage dirs exist
    settings.ensure_dirs()

    # Initialise database
    await db.init(settings.DB_PATH)
    await models.seed_personas(await db.get().__anext__())

    # Load ML models in thread pool (blocking operations)
    print(f"Loading STT model ({settings.STT_MODEL_SIZE})...")
    from .services.stt import load_stt_model
    app_state.stt_model = await asyncio.to_thread(load_stt_model, settings.STT_MODEL_SIZE)
    print("STT model ready.")

    print(f"Loading Kokoro TTS ({settings.DEFAULT_VOICE})...")
    from .services.tts import load_kokoro_pipeline
    app_state.kokoro_pipeline = await asyncio.to_thread(
        load_kokoro_pipeline, settings.DEFAULT_VOICE
    )
    print("Kokoro ready.")

    yield

    # Shutdown
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

# Allow the frontend server to call us
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── routers ────────────────────────────────────────────────────────────────────

app.include_router(internal.router)
app.include_router(lessons.router)
app.include_router(personas.router)
app.include_router(voices.router)
app.include_router(ws_session.router)


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
    )
