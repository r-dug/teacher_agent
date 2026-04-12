
# ── TTS Providers ─────────────────

# ── OpenAI TTS ────────────────────

# OpenAI `/v1/audio/speech` PCM output is 24 kHz mono.
OPENAI_TTS_SAMPLE_RATE = 24000

# Built-in voice IDs for gpt-4o-mini-tts / tts-1 / tts-1-hd.
# Source: https://platform.openai.com/docs/guides/text-to-speech
# OpenAI has no programmatic endpoint to list voices — this is
# hand-maintained from the docs.  Last verified 2026-04-12.
OPENAI_TTS_VOICES: dict[str, str] = {
    "alloy": "en",
    "ash": "en",
    "ballad": "en",
    "cedar": "en",
    "coral": "en",
    "echo": "en",
    "fable": "en",
    "marin": "en",
    "nova": "en",
    "onyx": "en",
    "sage": "en",
    "shimmer": "en",
    "verse": "en",
}
DEFAULT_OPENAI_TTS_VOICE = "alloy"

# ── Kokoro TTS ───────────────────

KOKORO_SAMPLE_RATE = 24000

# Maps voice name → language code ('a'=American English, 'b'=British English)
KOKORO_VOICES: dict[str, str] = {
    "af_heart":  "a",   # American female, very natural
    "af_bella":  "a",   # American female
    "am_adam":   "a",   # American male
    "bf_emma":   "b",   # British female
    "bm_george": "b",   # British male
}
DEFAULT_KOKORO_VOICE = "af_bella"

# ── STT Providers ────────────────

SUPPORTED_STT_PROVIDERS = frozenset({"local", "openai"})

OPENAI_STT_MODELS = [
    "gpt-4o-mini-transcribe",
    "gpt-4o-transcribe",
    "whisper-1",
]