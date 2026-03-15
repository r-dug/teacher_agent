"""
In-memory session store for the frontend server.

Tracks which session_ids are valid and their associated metadata.
Sessions are created here (via a backend API call) and lost on frontend restart —
clients must reconnect and create a new session.

This will be replaced with a persistent store (Redis or DB) when multi-instance
deployment is needed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SessionEntry:
    session_id: str
    user_id: str
    email: str = ""
    is_admin: bool = False
    created_at: float = field(default_factory=time.monotonic)
    last_turn_id: Optional[str] = None
    turn_status: str = "idle"  # 'idle' | 'running' | 'complete' | 'failed'


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionEntry] = {}

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def add(
        self, session_id: str, user_id: str, email: str = "", is_admin: bool = False
    ) -> SessionEntry:
        entry = SessionEntry(
            session_id=session_id, user_id=user_id, email=email, is_admin=is_admin
        )
        self._sessions[session_id] = entry
        return entry

    def get(self, session_id: str) -> Optional[SessionEntry]:
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def __len__(self) -> int:
        return len(self._sessions)

    # ── turn tracking (for crash recovery) ────────────────────────────────────

    def set_turn(self, session_id: str, turn_id: str, status: str) -> None:
        if entry := self._sessions.get(session_id):
            entry.last_turn_id = turn_id
            entry.turn_status = status

    def get_turn_status(self, session_id: str) -> tuple[Optional[str], str]:
        """Return (last_turn_id, turn_status) or (None, 'idle')."""
        if entry := self._sessions.get(session_id):
            return entry.last_turn_id, entry.turn_status
        return None, "idle"


# Module-level singleton shared across all routes
store = SessionStore()
