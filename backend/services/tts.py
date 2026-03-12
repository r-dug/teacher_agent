"""TTS service: Kokoro pipeline loader for use in the backend."""

from __future__ import annotations

from shared.constants import KOKORO_VOICES, DEFAULT_KOKORO_VOICE


def load_kokoro_pipeline(voice: str = DEFAULT_KOKORO_VOICE):
    """
    Load and return a KPipeline for the given voice (blocking; call at startup).

    The pipeline is language-code scoped.  When sessions request different voices
    with the same language code, the same pipeline instance can be reused.
    For simplicity the backend loads a single pipeline at startup using the
    default voice's language code; the lang_code is 'a' (American English) for
    all default voices.
    """
    from kokoro import KPipeline
    lang_code = KOKORO_VOICES.get(voice, "a")
    return KPipeline(lang_code=lang_code)
