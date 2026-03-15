"""
Frontend server entry point.

Runs on 0.0.0.0:8000 (LAN-accessible).  Acts as a BFF (Backend for Frontend):
  - Validates session tokens on every request
  - Rate-limits per session
  - Proxies REST calls and WebSocket connections to the backend (127.0.0.1:8001)
  - Issues short-lived upload tokens for direct client→backend PDF uploads

Run with:
    python -m frontend.main
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .config import settings
from . import http_client
from .routers import auth, sessions, courses, lessons, personas, voices, ws_proxy, usage

_STATIC_DIR = Path(__file__).parent / "static"


# ── lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.ENV == "production" and settings.ALLOWED_ORIGINS == ["*"]:
        raise RuntimeError(
            "ALLOWED_ORIGINS must be set to an explicit origin list in production "
            "(current value is '*'). Set ALLOWED_ORIGINS=https://yourdomain.com"
        )
    await http_client.init(settings.BACKEND_HTTP)
    yield
    await http_client.close()


# ── app ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="pdf-to-audio Frontend Server",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)

class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add HSTS, CSP, and MIME-type-sniffing-prevention headers."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "   # needed by Vite-built assets in prod
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "media-src 'self' blob:; "
            "connect-src 'self' wss: ws:; "
            "font-src 'self' data:; "
            "object-src 'none'; "
            "frame-ancestors 'none'"
        )
        return response


app.add_middleware(_SecurityHeadersMiddleware)

_wildcard_origins = settings.ALLOWED_ORIGINS == ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    # allow_credentials=True is incompatible with wildcard origins (CORS spec).
    # Sessions use a custom header, not cookies, so False is fine either way.
    allow_credentials=not _wildcard_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── routers ────────────────────────────────────────────────────────────────────

app.include_router(auth.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(courses.router, prefix="/api")
app.include_router(lessons.router, prefix="/api")
app.include_router(personas.router, prefix="/api")
app.include_router(voices.router, prefix="/api")
app.include_router(ws_proxy.router)  # /ws/... — no /api prefix
app.include_router(usage.router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── static files (production build) ───────────────────────────────────────────
# Mount the built React app. This is only active when the static/ directory
# exists (i.e. after `npm run build`). In dev, Vite's dev server is used instead.

if _STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):  # noqa: ARG001
        """Return index.html for all non-API routes (React Router SPA)."""
        index = _STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(index)
        return {"error": "Static build not found — run `npm run build` in client/"}


# ── entrypoint ─────────────────────────────────────────────────────────────────

_CERTS_DIR = Path(__file__).parents[1] / "certs"

if __name__ == "__main__":
    # Auto-detect certs from the project certs/ directory if they exist,
    # falling back to explicit env vars (SSL_CERTFILE / SSL_KEYFILE).
    _cert = _CERTS_DIR / "cert.pem"
    _key = _CERTS_DIR / "key.pem"
    ssl_certfile = str(_cert) if _cert.exists() else (settings.SSL_CERTFILE or None)
    ssl_keyfile = str(_key) if _key.exists() else (settings.SSL_KEYFILE or None)

    uvicorn.run(
        "frontend.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=False,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
    )
