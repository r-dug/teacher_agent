"""Tests for TTS services (provider selection and adapters)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import numpy as np

from backend.services.tts import (
    KokoroTTSProvider,
    OpenAITTSProvider,
    _decode_openai_audio,
    load_kokoro_pipeline,
    select_tts_provider,
)
from shared.constants import KOKORO_SAMPLE_RATE

def test_load_kokoro_pipeline_uses_lang_code():
    """load_kokoro_pipeline should resolve voice name to lang_code."""
    mock_pipeline_cls = MagicMock()

    with patch("backend.services.tts.KPipeline", mock_pipeline_cls, create=True):
        # Patch the import inside the function
        import backend.services.tts as tts_mod
        with patch.dict("sys.modules", {"kokoro": MagicMock(KPipeline=mock_pipeline_cls)}):
            tts_mod.load_kokoro_pipeline("af_bella")

    # Verify KPipeline was called with lang_code='a' (American English)
    mock_pipeline_cls.assert_called_once_with(lang_code="a")


def test_load_kokoro_pipeline_unknown_voice_defaults():
    """Unknown voice should default to lang_code 'a'."""
    mock_pipeline_cls = MagicMock()

    import backend.services.tts as tts_mod
    with patch.dict("sys.modules", {"kokoro": MagicMock(KPipeline=mock_pipeline_cls)}):
        tts_mod.load_kokoro_pipeline("unknown_voice_xyz")

    mock_pipeline_cls.assert_called_once_with(lang_code="a")


def test_select_tts_provider_explicit_override_wins():
    assert select_tts_provider("kokoro", "development") == "kokoro"
    assert select_tts_provider("openai", "production") == "openai"


def test_select_tts_provider_env_defaulting():
    assert select_tts_provider(None, "production") == "kokoro"
    assert select_tts_provider(None, "staging") == "openai"


def test_kokoro_provider_synthesize_returns_float32_pcm():
    class _Tensor:
        def __init__(self, arr: np.ndarray):
            self._arr = arr

        def numpy(self) -> np.ndarray:
            return self._arr

    def _fake_pipeline(text: str, voice: str):
        assert text == "hello"
        assert voice == "af_bella"
        yield "", None, _Tensor(np.array([0.2, -0.2], dtype=np.float32))
        yield "", None, _Tensor(np.array([0.1], dtype=np.float32))

    provider = KokoroTTSProvider(pipeline=_fake_pipeline, default_voice="af_bella")
    out = provider.synthesize("hello", "af_bella")

    assert out.sample_rate == KOKORO_SAMPLE_RATE
    assert out.voice == "af_bella"
    assert out.characters == 5
    assert out.audio.dtype == np.float32
    np.testing.assert_allclose(out.audio, np.array([0.2, -0.2, 0.1], dtype=np.float32))


def test_openai_provider_pcm_parsing_and_request_shape(monkeypatch):
    captured: dict = {}

    @dataclass
    class _Resp:
        status_code: int
        content: bytes
        text: str = ""

        def json(self):
            return {}

    class _Client:
        def __init__(self, timeout: float):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url: str, headers: dict, json: dict):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            pcm = np.array([0, 16384, -16384], dtype=np.int16).tobytes()
            return _Resp(status_code=200, content=pcm)

    monkeypatch.setattr("backend.services.tts.httpx.Client", _Client)

    provider = OpenAITTSProvider(
        api_key="test-key",
        model="gpt-4o-mini-tts",
        default_voice="alloy",
        response_format="pcm16",
        timeout_seconds=7.5,
        max_retries=0,
        cost_per_minute_usd=0.015,
    )
    out = provider.synthesize("hello world", "alloy")

    assert captured["url"] == "https://api.openai.com/v1/audio/speech"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["model"] == "gpt-4o-mini-tts"
    assert captured["json"]["voice"] == "alloy"
    assert captured["json"]["format"] == "pcm16"
    assert out.sample_rate == KOKORO_SAMPLE_RATE
    np.testing.assert_allclose(out.audio, np.array([0.0, 0.5, -0.5], dtype=np.float32), atol=1e-4)
    assert out.estimated_cost_usd > 0


def test_decode_openai_audio_wav(monkeypatch):
    captured: dict[str, object] = {}

    class _Sf:
        @staticmethod
        def read(_bio, dtype: str = "float32"):
            captured["dtype"] = dtype
            return np.array([[0.25, -0.25], [0.1, -0.1]], dtype=np.float32), 22050

    monkeypatch.setitem(__import__("sys").modules, "soundfile", _Sf)
    audio, sr = _decode_openai_audio(b"RIFFxxxxWAVEpayload", expected_format="wav")

    assert sr == 22050
    assert captured["dtype"] == "float32"
    np.testing.assert_allclose(audio, np.array([0.0, 0.0], dtype=np.float32))


def test_decode_openai_audio_pcm16():
    pcm = np.array([0, 16384, -16384], dtype=np.int16).tobytes()
    audio, sr = _decode_openai_audio(pcm, expected_format="pcm16")
    assert sr == KOKORO_SAMPLE_RATE
    np.testing.assert_allclose(audio, np.array([0.0, 0.5, -0.5], dtype=np.float32), atol=1e-4)
