"""
Logging configuration for the backend server.

Call configure_logging() once at process startup, before any other imports
that might trigger logging.

Environment variables:
    LOG_LEVEL   — root log level (DEBUG, INFO, WARNING, ERROR). Default: INFO.
    LOG_FILE    — set to 1 to write JSON-lines log to storage/backend.log.

Console output uses a human-readable format. File output uses JSON lines
(one object per record) so logs can be parsed, filtered, and fed to tools.

Uvicorn's own loggers (uvicorn, uvicorn.access, uvicorn.error) are folded
into this config so everything comes out consistently.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path


# ── JSON formatter (for file output) ──────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": round(time.time(), 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry)


# ── console formatter (human-readable) ────────────────────────────────────────

_CONSOLE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_CONSOLE_DATE = "%H:%M:%S"


# ── public API ─────────────────────────────────────────────────────────────────

def configure_logging(storage_dir: Path | None = None) -> None:
    """
    Configure root logger + uvicorn loggers.

    Safe to call multiple times — subsequent calls are no-ops.
    """
    root = logging.getLogger()
    if root.handlers:
        return  # already configured

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    # Console handler — always on.
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT, datefmt=_CONSOLE_DATE))
    console.setLevel(level)
    root.addHandler(console)
    root.setLevel(level)

    # File handler — opt-in via LOG_FILE=1.
    if os.getenv("LOG_FILE"):
        log_dir = storage_dir or Path("./storage")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "backend.log"

        file_handler = logging.FileHandler(log_path)
        file_handler.setFormatter(_JsonFormatter())
        file_handler.setLevel(level)
        root.addHandler(file_handler)

    # Fold uvicorn's loggers into our config so they share format and level.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_log = logging.getLogger(name)
        uv_log.handlers = []
        uv_log.propagate = True
