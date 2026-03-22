"""
Dev-mode WebSocket message logger.

Gated on the WS_DEBUG_LOG environment variable. When set, logs every WS
frame (both directions) as a JSON line to storage/ws_debug.log.

Audio/binary payloads are stripped to avoid bloating the log — only the
event type and non-data metadata fields are recorded.

Usage:
    WS_DEBUG_LOG=1 uv run python -m frontend.main
Then tail the log:
    tail -f storage/ws_debug.log | python -m json.tool
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

# Fields that contain large base64 payloads — strip from logs.
_STRIP_FIELDS = {"data", "frames", "video_frames"}

_enabled = bool(os.getenv("WS_DEBUG_LOG"))
_log: logging.Logger | None = None


def _get_logger() -> logging.Logger:
    global _log
    if _log is not None:
        return _log

    log_path = Path(__file__).parent.parent / "storage" / "ws_debug.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("ws_debug")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

    _log = logger
    return _log


def _scrub(msg: dict) -> dict:
    """Return a copy of msg with large payload fields replaced by their byte length."""
    out = {}
    for k, v in msg.items():
        if k in _STRIP_FIELDS and isinstance(v, str):
            out[k] = f"<{len(v)} chars>"
        elif k in _STRIP_FIELDS and isinstance(v, list):
            out[k] = f"<list of {len(v)}>"
        else:
            out[k] = v
    return out


def log_frame(direction: str, session_id: str, raw: str | bytes) -> None:
    """Log one WS frame. direction is 'c→b' or 'b→c'."""
    if not _enabled:
        return

    entry: dict = {
        "ts": round(time.time(), 3),
        "dir": direction,
        "session": session_id[:8],  # first 8 chars is enough for debugging
    }

    if isinstance(raw, bytes):
        entry["type"] = "binary"
        entry["size"] = len(raw)
    else:
        entry["size"] = len(raw)
        try:
            msg = json.loads(raw)
            entry["event"] = msg.get("event", "?")
            entry["meta"] = _scrub({k: v for k, v in msg.items() if k != "event"})
        except (json.JSONDecodeError, AttributeError):
            entry["type"] = "non-json"

    _get_logger().debug(json.dumps(entry))
