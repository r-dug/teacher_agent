"""Shared constants used across the pdf-to-audio application suite."""

# ── Kokoro TTS ─────────────────────────────────────────────────────────────────

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

# ── OpenAI TTS ─────────────────────────────────────────────────────────────────

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

# ── Piper TTS ─────────────────────────────────────────────────────────────────

# Maps shorthand name → Piper model ID
PIPER_VOICES: dict[str, str] = {
    "en-lessac":   "en_US-lessac-medium",     # natural US female (default)
    "en-ryan":     "en_US-ryan-high",          # natural US male
    "en-amy":      "en_US-amy-medium",         # US female
    "en-gb-alan":  "en_GB-alan-medium",        # British male
    "en-gb-jenny": "en_GB-jenny_dioco-medium", # British female
}
DEFAULT_PIPER_VOICE = "en-amy"

# ── Wake word detection ───────────────────────────────────────────────────────

WAKE_WORD_CHUNK = 1280       # 80 ms at 16 kHz — required by openwakeword
SILENCE_POLL_MS = 300        # how often to poll for silence (ms)
