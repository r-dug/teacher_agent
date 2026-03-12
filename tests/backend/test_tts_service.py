"""Tests for the TTS service (mocked Kokoro)."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from backend.services.tts import load_kokoro_pipeline


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
