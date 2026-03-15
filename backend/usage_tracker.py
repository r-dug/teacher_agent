"""
Usage tracking for API calls, local STT, and local TTS.

Architecture
------------
* Worker threads (STT/TTS/LLM) call record_api / record_stt / record_tts —
  these are *synchronous* and write directly to SQLite via a thread-safe
  sqlite3 connection (WAL mode).  No async required in hot path.

* A background asyncio task (run from main.py lifespan) calls
  aggregate_minutes() every 60 s.  It reads unaggregated raw rows, rolls
  them into usage_minutes, then marks them aggregated and prunes rows
  older than 48 h.

* On the 1st of each calendar month (checked at startup and daily),
  roll_month_to_hours() aggregates all usage_minutes from the previous
  month into usage_hours, then deletes those minute rows.

* The in-memory TokenUsageTracker (legacy, kept for the sidebar widget)
  is also updated on each record_api call so TokenUsageDisplay still works.

Pricing
-------
Approximate USD per token; update as Anthropic changes pricing.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

# ── Pricing ───────────────────────────────────────────────────────────────────

_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6":  {"input": 3e-6,    "output": 15e-6,   "cache_read": 0.30e-6,  "cache_write": 3.75e-6},
    "claude-opus-4-6":    {"input": 15e-6,   "output": 75e-6,   "cache_read": 1.50e-6,  "cache_write": 18.75e-6},
    "claude-haiku-4-5":   {"input": 0.25e-6, "output": 1.25e-6, "cache_read": 0.03e-6,  "cache_write": 0.30e-6},
    "claude-haiku-4-5-20251001": {"input": 0.25e-6, "output": 1.25e-6, "cache_read": 0.03e-6, "cache_write": 0.30e-6},
}
_DEFAULT_PRICING = {"input": 3e-6, "output": 15e-6, "cache_read": 0.30e-6, "cache_write": 3.75e-6}


def _api_cost(model: str, inp: int, out: int, cr: int, cw: int) -> float:
    p = _PRICING.get(model) or _PRICING.get(model.split("-20")[0], _DEFAULT_PRICING)
    return inp * p["input"] + out * p["output"] + cr * p["cache_read"] + cw * p["cache_write"]


# ── In-memory summary (for sidebar widget, backward-compat) ──────────────────

@dataclass
class _ApiRecord:
    call_type: str
    model: str
    session_id: str | None
    timestamp: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int

    @property
    def estimated_cost_usd(self) -> float:
        return _api_cost(self.model, self.input_tokens, self.output_tokens,
                         self.cache_read_tokens, self.cache_write_tokens)


def _agg(records: list[_ApiRecord]) -> dict:
    inp = sum(r.input_tokens for r in records)
    out = sum(r.output_tokens for r in records)
    cr  = sum(r.cache_read_tokens for r in records)
    cw  = sum(r.cache_write_tokens for r in records)
    return {
        "calls": len(records),
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_tokens": cr,
        "cache_write_tokens": cw,
        "total_tokens": inp + out,
        "estimated_cost_usd": round(sum(r.estimated_cost_usd for r in records), 6),
    }


# ── Main tracker ─────────────────────────────────────────────────────────────

class UsageTracker:
    """
    Thread-safe usage tracker with SQLite persistence.

    Call init(db_path) once at startup before any records.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        # In-memory for sidebar widget
        self._api_records: list[_ApiRecord] = []

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def init(self, db_path: Path | str) -> None:
        """Open a dedicated sync SQLite connection for usage writes."""
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")

    def close(self) -> None:
        if self._db:
            self._db.close()
            self._db = None

    # ── Record helpers ────────────────────────────────────────────────────────

    def record_api(
        self,
        call_type: str,
        model: str,
        usage,  # anthropic Usage object
        user_id: str = "",
        session_id: str | None = None,
    ) -> None:
        """Record an Anthropic API call.  Safe to call from any thread."""
        inp = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        cr  = getattr(usage, "cache_read_input_tokens", 0) or 0
        cw  = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cost = _api_cost(model, inp, out, cr, cw)
        ts = time.time()

        # In-memory (sidebar widget)
        with self._lock:
            self._api_records.append(_ApiRecord(
                call_type=call_type, model=model, session_id=session_id,
                timestamp=ts, input_tokens=inp, output_tokens=out,
                cache_read_tokens=cr, cache_write_tokens=cw,
            ))

        # DB (persistent)
        self._insert_raw(
            ts=ts, user_id=user_id, event_type="api",
            call_type=call_type, model=model,
            input_tokens=inp, output_tokens=out,
            cache_read_tokens=cr, cache_write_tokens=cw, cost_usd=cost,
        )

    def record_stt(
        self,
        stt_model: str,
        stt_language: str,
        audio_seconds: float,
        transcription_ms: int,
        user_id: str = "",
    ) -> None:
        """Record a local STT transcription.  Safe to call from any thread."""
        self._insert_raw(
            ts=time.time(), user_id=user_id, event_type="stt",
            stt_model=stt_model, stt_language=stt_language,
            audio_seconds=audio_seconds, transcription_ms=transcription_ms,
        )

    def record_tts(
        self,
        tts_voice: str,
        tts_characters: int,
        tts_audio_seconds: float,
        tts_synthesis_ms: int,
        user_id: str = "",
    ) -> None:
        """Record a local TTS synthesis.  Safe to call from any thread."""
        self._insert_raw(
            ts=time.time(), user_id=user_id, event_type="tts",
            tts_voice=tts_voice, tts_characters=tts_characters,
            tts_audio_seconds=tts_audio_seconds, tts_synthesis_ms=tts_synthesis_ms,
        )

    # ── In-memory summary (backward-compat for TokenUsageDisplay) ─────────────

    def summary(self) -> dict:
        with self._lock:
            records = list(self._api_records)
        by_type: dict[str, list[_ApiRecord]] = defaultdict(list)
        by_model: dict[str, list[_ApiRecord]] = defaultdict(list)
        for r in records:
            by_type[r.call_type].append(r)
            by_model[r.model].append(r)
        return {
            "totals": _agg(records),
            "by_call_type": {ct: _agg(recs) for ct, recs in sorted(by_type.items())},
            "by_model": {m: _agg(recs) for m, recs in sorted(by_model.items())},
        }

    def reset(self) -> None:
        with self._lock:
            self._api_records.clear()

    # ── Background aggregation ────────────────────────────────────────────────

    def aggregate_minutes(self) -> int:
        """
        Roll unaggregated raw rows into usage_minutes.  Returns rows processed.
        Call from an asyncio background task every 60 s.
        Only aggregates rows older than 60 s (ensures the current minute is complete).
        """
        if self._db is None:
            return 0
        cutoff = time.time() - 60
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM usage_raw WHERE aggregated = 0 AND ts < ?",
                (cutoff,),
            ).fetchall()
            if not rows:
                return 0

            # Group by (minute_ts, user_id, event_type, call_type, model,
            #            stt_model, stt_language, tts_voice)
            buckets: dict[tuple, dict] = {}
            ids = [r[0] for r in rows]

            # Re-fetch with row_factory so columns are accessible by name
            self._db.row_factory = sqlite3.Row
            rows = self._db.execute(
                f"SELECT * FROM usage_raw WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            ).fetchall()
            self._db.row_factory = None

            for r in rows:
                minute_ts = int(r["ts"] // 60) * 60
                key = (
                    minute_ts,
                    r["user_id"], r["event_type"],
                    r["call_type"], r["model"],
                    r["stt_model"], r["stt_language"], r["tts_voice"],
                )
                if key not in buckets:
                    buckets[key] = {
                        "calls": 0, "input_tokens": 0, "output_tokens": 0,
                        "cache_read_tokens": 0, "cache_write_tokens": 0,
                        "cost_usd": 0.0, "audio_seconds": 0.0,
                        "transcription_ms": 0, "tts_characters": 0,
                        "tts_audio_seconds": 0.0, "tts_synthesis_ms": 0,
                    }
                b = buckets[key]
                b["calls"] += 1
                b["input_tokens"] += r["input_tokens"]
                b["output_tokens"] += r["output_tokens"]
                b["cache_read_tokens"] += r["cache_read_tokens"]
                b["cache_write_tokens"] += r["cache_write_tokens"]
                b["cost_usd"] += r["cost_usd"]
                b["audio_seconds"] += r["audio_seconds"]
                b["transcription_ms"] += r["transcription_ms"]
                b["tts_characters"] += r["tts_characters"]
                b["tts_audio_seconds"] += r["tts_audio_seconds"]
                b["tts_synthesis_ms"] += r["tts_synthesis_ms"]

            # Upsert into usage_minutes
            for key, b in buckets.items():
                (minute_ts, user_id, event_type, call_type, model,
                 stt_model, stt_language, tts_voice) = key
                self._db.execute(
                    """INSERT INTO usage_minutes
                       (minute_ts, user_id, event_type, call_type, model,
                        stt_model, stt_language, tts_voice,
                        calls, input_tokens, output_tokens,
                        cache_read_tokens, cache_write_tokens, cost_usd,
                        audio_seconds, transcription_ms,
                        tts_characters, tts_audio_seconds, tts_synthesis_ms)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(minute_ts, user_id, event_type, call_type, model,
                                   stt_model, stt_language, tts_voice)
                       DO UPDATE SET
                         calls              = calls + excluded.calls,
                         input_tokens       = input_tokens + excluded.input_tokens,
                         output_tokens      = output_tokens + excluded.output_tokens,
                         cache_read_tokens  = cache_read_tokens + excluded.cache_read_tokens,
                         cache_write_tokens = cache_write_tokens + excluded.cache_write_tokens,
                         cost_usd           = cost_usd + excluded.cost_usd,
                         audio_seconds      = audio_seconds + excluded.audio_seconds,
                         transcription_ms   = transcription_ms + excluded.transcription_ms,
                         tts_characters     = tts_characters + excluded.tts_characters,
                         tts_audio_seconds  = tts_audio_seconds + excluded.tts_audio_seconds,
                         tts_synthesis_ms   = tts_synthesis_ms + excluded.tts_synthesis_ms""",
                    (minute_ts, user_id, event_type, call_type, model,
                     stt_model, stt_language, tts_voice,
                     b["calls"], b["input_tokens"], b["output_tokens"],
                     b["cache_read_tokens"], b["cache_write_tokens"], b["cost_usd"],
                     b["audio_seconds"], b["transcription_ms"],
                     b["tts_characters"], b["tts_audio_seconds"], b["tts_synthesis_ms"]),
                )

            # Mark raw rows as aggregated; prune rows older than 48 h
            self._db.execute(
                f"UPDATE usage_raw SET aggregated = 1 WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            )
            cutoff_48h = time.time() - 48 * 3600
            self._db.execute(
                "DELETE FROM usage_raw WHERE aggregated = 1 AND ts < ?",
                (cutoff_48h,),
            )
            self._db.commit()

        return len(ids)

    def roll_month_to_hours(self) -> int:
        """
        Aggregate previous calendar month's usage_minutes → usage_hours, then
        delete those minute rows.  Returns number of minute rows rolled up.
        Call at startup and once per day.
        """
        if self._db is None:
            return 0
        import calendar
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        # Only roll if we are in a new month (i.e. previous month exists in usage_minutes)
        # Determine start/end of last month in Unix timestamps
        first_of_this_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        if now.month == 1:
            first_of_last_month = datetime(now.year - 1, 12, 1, tzinfo=timezone.utc)
        else:
            first_of_last_month = datetime(now.year, now.month - 1, 1, tzinfo=timezone.utc)
        ts_start = int(first_of_last_month.timestamp())
        ts_end   = int(first_of_this_month.timestamp())

        with self._lock:
            self._db.row_factory = sqlite3.Row
            rows = self._db.execute(
                "SELECT * FROM usage_minutes WHERE minute_ts >= ? AND minute_ts < ?",
                (ts_start, ts_end),
            ).fetchall()
            self._db.row_factory = None
            if not rows:
                return 0

            buckets: dict[tuple, dict] = {}
            for r in rows:
                hour_ts = int(r["minute_ts"] // 3600) * 3600
                key = (
                    hour_ts,
                    r["user_id"], r["event_type"],
                    r["call_type"], r["model"],
                    r["stt_model"], r["stt_language"], r["tts_voice"],
                )
                if key not in buckets:
                    buckets[key] = {k: 0 for k in (
                        "calls", "input_tokens", "output_tokens",
                        "cache_read_tokens", "cache_write_tokens",
                        "tts_characters", "tts_synthesis_ms", "transcription_ms",
                    )}
                    buckets[key].update({"cost_usd": 0.0, "audio_seconds": 0.0, "tts_audio_seconds": 0.0})
                b = buckets[key]
                for field in ("calls", "input_tokens", "output_tokens",
                              "cache_read_tokens", "cache_write_tokens",
                              "tts_characters", "tts_synthesis_ms", "transcription_ms"):
                    b[field] += r[field]
                for field in ("cost_usd", "audio_seconds", "tts_audio_seconds"):
                    b[field] += r[field]

            for key, b in buckets.items():
                (hour_ts, user_id, event_type, call_type, model,
                 stt_model, stt_language, tts_voice) = key
                self._db.execute(
                    """INSERT INTO usage_hours
                       (hour_ts, user_id, event_type, call_type, model,
                        stt_model, stt_language, tts_voice,
                        calls, input_tokens, output_tokens,
                        cache_read_tokens, cache_write_tokens, cost_usd,
                        audio_seconds, transcription_ms,
                        tts_characters, tts_audio_seconds, tts_synthesis_ms)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(hour_ts, user_id, event_type, call_type, model,
                                   stt_model, stt_language, tts_voice)
                       DO UPDATE SET
                         calls              = calls + excluded.calls,
                         input_tokens       = input_tokens + excluded.input_tokens,
                         output_tokens      = output_tokens + excluded.output_tokens,
                         cache_read_tokens  = cache_read_tokens + excluded.cache_read_tokens,
                         cache_write_tokens = cache_write_tokens + excluded.cache_write_tokens,
                         cost_usd           = cost_usd + excluded.cost_usd,
                         audio_seconds      = audio_seconds + excluded.audio_seconds,
                         transcription_ms   = transcription_ms + excluded.transcription_ms,
                         tts_characters     = tts_characters + excluded.tts_characters,
                         tts_audio_seconds  = tts_audio_seconds + excluded.tts_audio_seconds,
                         tts_synthesis_ms   = tts_synthesis_ms + excluded.tts_synthesis_ms""",
                    (hour_ts, user_id, event_type, call_type, model,
                     stt_model, stt_language, tts_voice,
                     b["calls"], b["input_tokens"], b["output_tokens"],
                     b["cache_read_tokens"], b["cache_write_tokens"], b["cost_usd"],
                     b["audio_seconds"], b["transcription_ms"],
                     b["tts_characters"], b["tts_audio_seconds"], b["tts_synthesis_ms"]),
                )

            n = len(rows)
            self._db.execute(
                "DELETE FROM usage_minutes WHERE minute_ts >= ? AND minute_ts < ?",
                (ts_start, ts_end),
            )
            self._db.commit()

        return n

    # ── Query helpers (called from async routes via asyncio.to_thread) ─────────

    def query_series(
        self,
        from_ts: float,
        to_ts: float,
        granularity: str = "minute",   # 'minute' | 'hour'
        user_id: str | None = None,
    ) -> list[dict]:
        """
        Return time-series rows covering [from_ts, to_ts).
        Reads from usage_minutes (within last month) or usage_hours (older).
        """
        if self._db is None:
            return []
        table = "usage_minutes" if granularity == "minute" else "usage_hours"
        ts_col = "minute_ts" if granularity == "minute" else "hour_ts"
        params: list = [from_ts, to_ts]
        user_clause = ""
        if user_id is not None:
            user_clause = " AND user_id = ?"
            params.append(user_id)
        self._db.row_factory = sqlite3.Row
        rows = self._db.execute(
            f"SELECT * FROM {table} WHERE {ts_col} >= ? AND {ts_col} < ?{user_clause} ORDER BY {ts_col}",
            params,
        ).fetchall()
        self._db.row_factory = None
        return [dict(r) for r in rows]

    def query_live(self) -> list[dict]:
        """Return raw events from the last 90 seconds (for the live feed)."""
        if self._db is None:
            return []
        cutoff = time.time() - 90
        self._db.row_factory = sqlite3.Row
        rows = self._db.execute(
            "SELECT * FROM usage_raw WHERE ts > ? ORDER BY ts DESC LIMIT 100",
            (cutoff,),
        ).fetchall()
        self._db.row_factory = None
        return [dict(r) for r in rows]

    def query_totals(
        self,
        window_seconds: float | None = None,
        user_id: str | None = None,
    ) -> dict:
        """
        Aggregate totals over a time window.  Queries usage_minutes + any
        unaggregated raw rows to include the current minute.
        """
        if self._db is None:
            return {}
        now = time.time()
        from_ts = (now - window_seconds) if window_seconds else 0.0
        user_clause = "" if user_id is None else " AND user_id = ?"
        params_u: list = [from_ts] + ([user_id] if user_id else [])

        self._db.row_factory = sqlite3.Row
        minute_rows = self._db.execute(
            f"SELECT * FROM usage_minutes WHERE minute_ts >= ?{user_clause}",
            params_u,
        ).fetchall()
        raw_rows = self._db.execute(
            f"SELECT * FROM usage_raw WHERE ts >= ? AND aggregated = 0{user_clause}",
            params_u,
        ).fetchall()
        self._db.row_factory = None

        return _sum_rows([dict(r) for r in minute_rows] + [dict(r) for r in raw_rows])

    # ── Private ────────────────────────────────────────────────────────────────

    def _insert_raw(
        self,
        ts: float,
        user_id: str,
        event_type: str,
        call_type: str = "",
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cost_usd: float = 0.0,
        stt_model: str = "",
        stt_language: str = "",
        audio_seconds: float = 0.0,
        transcription_ms: int = 0,
        tts_voice: str = "",
        tts_characters: int = 0,
        tts_audio_seconds: float = 0.0,
        tts_synthesis_ms: int = 0,
    ) -> None:
        if self._db is None:
            return
        with self._lock:
            self._db.execute(
                """INSERT INTO usage_raw
                   (ts, user_id, event_type,
                    call_type, model,
                    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd,
                    stt_model, stt_language, audio_seconds, transcription_ms,
                    tts_voice, tts_characters, tts_audio_seconds, tts_synthesis_ms)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ts, user_id, event_type,
                 call_type, model,
                 input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd,
                 stt_model, stt_language, audio_seconds, transcription_ms,
                 tts_voice, tts_characters, tts_audio_seconds, tts_synthesis_ms),
            )
            self._db.commit()


# ── Utility ───────────────────────────────────────────────────────────────────

def _sum_rows(rows: list[dict]) -> dict:
    totals: dict[str, float | int] = {
        "calls": 0, "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.0,
        "audio_seconds": 0.0, "transcription_ms": 0,
        "tts_characters": 0, "tts_audio_seconds": 0.0, "tts_synthesis_ms": 0,
    }
    for r in rows:
        for k in totals:
            totals[k] = totals[k] + (r.get(k) or 0)  # type: ignore[operator]
    return totals


# ── Backward-compat alias so existing imports of TokenUsageTracker still work ─

TokenUsageTracker = UsageTracker
