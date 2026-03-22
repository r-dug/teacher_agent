"""
Logging configuration for the frontend (BFF) server.

Mirrors backend/logging_config.py — kept separate since the two servers
run as independent processes.

Environment variables:
    LOG_LEVEL   — root log level (DEBUG, INFO, WARNING, ERROR). Default: INFO.
    LOG_FILE    — set to 1 to write JSON-lines log to storage/frontend.log.

Verification and password-reset URLs are logged at WARNING level by
email.py when RESEND_API_KEY is absent — they will appear in both the
console output and the log file, making dev-mode account setup scriptable.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path


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


_CONSOLE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_CONSOLE_DATE = "%H:%M:%S"


def configure_logging(storage_dir: Path | None = None) -> None:
    """Configure root logger + uvicorn loggers. Safe to call multiple times."""
    root = logging.getLogger()
    if root.handlers:
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT, datefmt=_CONSOLE_DATE))
    console.setLevel(level)
    root.addHandler(console)
    root.setLevel(level)

    if os.getenv("LOG_FILE"):
        log_dir = storage_dir or Path("./storage")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "frontend.log"

        file_handler = logging.FileHandler(log_path)
        file_handler.setFormatter(_JsonFormatter())
        file_handler.setLevel(level)
        root.addHandler(file_handler)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_log = logging.getLogger(name)
        uv_log.handlers = []
        uv_log.propagate = True
