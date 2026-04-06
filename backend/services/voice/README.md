# Voice Services

Speech-to-text (STT) and text-to-speech (TTS) pipeline with provider fallback and real-time streaming support.

## Components

```
voice/
  stt.py              Speech-to-text (Whisper via OpenAI API)
  tts.py              TTS provider interface + implementations
  tts_pipeline.py     → lives in agents/ — ordered fallback across TTS providers
  realtime.py         OpenAI Realtime API integration (voice-to-voice)
  phonetics.py        Phonetic comparison utilities (pronunciation feedback)
  utterance_gate.py   Voice activity detection / utterance boundary logic
  config.py           Voice-related settings and defaults
```

## STT Pipeline

`stt.py` wraps the OpenAI Whisper API. Audio arrives as PCM chunks from the client (via WebSocket), gets buffered, and sent for transcription when an utterance boundary is detected.

## TTS Pipeline

TTS uses an ordered provider list with permanent fallback. If the primary provider fails, the pipeline advances to the next provider for the remainder of the turn.

**Providers:**
- **OpenAI TTS** — high quality, ~200ms latency, charged per character
- **Kokoro** — local/self-hosted, lower latency, no per-character cost

The `TTSPipeline` (in `agents/tts_pipeline.py`) is initialized with `providers: list` and tries each in sequence. Audio chunks can exceed 1 MB from Kokoro, so they're split into 65536-sample sub-chunks before sending over WebSocket.

## Realtime Voice

`realtime.py` integrates the OpenAI Realtime API for direct voice-to-voice interaction (bypasses the STT + LLM + TTS chain). Used as an alternative mode when lower latency is critical.

## Phonetics

`phonetics.py` provides phonetic comparison utilities used for pronunciation feedback — comparing what the student said against expected pronunciation.

## Utterance Gate

`utterance_gate.py` handles voice activity detection logic — determining when the student has finished speaking so the agent can begin responding. This prevents the agent from interrupting mid-sentence.

## Audio Format

- Client sends: PCM 16-bit mono, 16kHz sample rate
- TTS outputs: PCM float32 numpy arrays, resampled to match client expectations
- WebSocket frame limit: 4 MB (`max_size=4*1024*1024`)
