"""Usage tracker tests for TTS cost telemetry."""

from __future__ import annotations

from tests.conftest import _resolve_connection_params
from backend.usage_tracker import UsageTracker


def _make_tracker() -> UsageTracker:
    dsn, kwargs = _resolve_connection_params()
    tracker = UsageTracker()
    tracker.init(dsn, password=kwargs.get("password"))
    return tracker


def test_record_tts_persists_cost_usd():
    tracker = _make_tracker()
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
    tts_rows = [r for r in rows if r["event_type"] == "tts"]
    assert tts_rows, "Expected a tts row"
    assert abs(tts_rows[-1]["cost_usd"] - 0.0123) < 1e-9


def test_record_stt_persists_cost_usd():
    tracker = _make_tracker()
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
    stt_rows = [r for r in rows if r["event_type"] == "stt"]
    assert stt_rows, "Expected an stt row"
    assert abs(stt_rows[-1]["cost_usd"] - 0.001) < 1e-9


def test_record_api_openai_model_uses_openai_pricing():
    tracker = _make_tracker()
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
    api_rows = [r for r in rows if r["event_type"] == "api"]
    assert api_rows, "Expected an api row"
    # $0.15 input + $0.60 output for 1M each on gpt-4o-mini.
    assert abs(api_rows[-1]["cost_usd"] - 0.75) < 1e-9
