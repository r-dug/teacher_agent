
# ── TTS Providers ─────────────────

# ── OpenAI TTS ────────────────────

# OpenAI `/v1/audio/speech` PCM output is 24 kHz mono.
OPENAI_TTS_SAMPLE_RATE = 24000

# Representative voice IDs for gpt-4o-mini-tts.
OPENAI_TTS_VOICES: dict[str, str] = {
    "alloy": "en",
    "ash": "en",
    "ballad": "en",
    "coral": "en",
    "echo": "en",
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