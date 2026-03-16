"""Backend configuration loaded from environment variables."""

from __future__ import annotations

import os
from pathlib import Path


class Settings:
    # Server
    HOST: str = os.getenv("BACKEND_HOST", "127.0.0.1")
    PORT: int = int(os.getenv("BACKEND_PORT", "8001"))

    # Storage
    STORAGE_DIR: Path = Path(os.getenv("STORAGE_DIR", "./storage"))
    DB_PATH: Path = Path(os.getenv("DB_PATH", "./storage/db.sqlite3"))

    # Models
    STT_MODEL_SIZE: str = os.getenv("STT_MODEL_SIZE", "base")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
    DEFAULT_VOICE: str = os.getenv("DEFAULT_VOICE", "af_heart")

    # Auth (prototype: all requests trusted from the frontend server)
    FRONTEND_ORIGIN: str = os.getenv("FRONTEND_ORIGIN", "http://127.0.0.1:8000")
    # Shared secret for BFF→backend calls.  When set, all inbound requests must
    # carry X-Internal-Token: <secret>.  Unset in dev (no check performed).
    BACKEND_SHARED_SECRET: str | None = os.getenv("BACKEND_SHARED_SECRET")

    def ensure_dirs(self) -> None:
        self.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
