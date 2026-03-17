"""Usage tracker tests for TTS cost telemetry."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from backend.usage_tracker import UsageTracker


def test_record_tts_persists_cost_usd(tmp_path):
    db_path = Path(tmp_path) / "usage.db"

    schema_path = Path(__file__).resolve().parents[2] / "backend" / "db" / "schema.sql"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema_path.read_text())
    conn.commit()
    conn.close()

    tracker = UsageTracker()
    tracker.init(db_path)
    tracker.record_tts(
        tts_voice="alloy",
        tts_characters=42,
        tts_audio_seconds=1.2,
        tts_synthesis_ms=120,
        cost_usd=0.0123,
        user_id="u1",
    )

    rows = tracker.query_live()
    tracker.close()

    assert rows, "Expected one usage_raw row."
    assert rows[0]["event_type"] == "tts"
    assert abs(rows[0]["cost_usd"] - 0.0123) < 1e-9


def test_record_stt_persists_cost_usd(tmp_path):
    db_path = Path(tmp_path) / "usage.db"

    schema_path = Path(__file__).resolve().parents[2] / "backend" / "db" / "schema.sql"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema_path.read_text())
    conn.commit()
    conn.close()

    tracker = UsageTracker()
    tracker.init(db_path)
    tracker.record_stt(
        stt_model="gpt-4o-mini-transcribe",
        stt_language="en",
        audio_seconds=10.0,
        transcription_ms=350,
        cost_usd=0.001,
        user_id="u1",
    )

    rows = tracker.query_live()
    tracker.close()

    assert rows, "Expected one usage_raw row."
    assert rows[0]["event_type"] == "stt"
    assert abs(rows[0]["cost_usd"] - 0.001) < 1e-9


def test_record_api_openai_model_uses_openai_pricing(tmp_path):
    db_path = Path(tmp_path) / "usage.db"

    schema_path = Path(__file__).resolve().parents[2] / "backend" / "db" / "schema.sql"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema_path.read_text())
    conn.commit()
    conn.close()

    tracker = UsageTracker()
    tracker.init(db_path)
    usage = type(
        "Usage",
        (),
        {
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    )()
    tracker.record_api(
        call_type="teach_turn",
        model="gpt-4o-mini",
        usage=usage,
        user_id="u1",
        session_id="s1",
    )
    rows = tracker.query_live()
    tracker.close()

    assert rows, "Expected one usage_raw row."
    assert rows[0]["event_type"] == "api"
    # $0.15 input + $0.60 output for 1M each on gpt-4o-mini.
    assert abs(rows[0]["cost_usd"] - 0.75) < 1e-9
