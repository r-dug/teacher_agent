"""Lightweight LLM gate for filtering irrelevant realtime voice utterances.

Classifies a VAD-transcribed utterance as one of:
  - "respond"  — relevant student speech, dispatch to the teaching agent
  - "ignore"   — background noise, self-talk, talking to someone else, coughs, etc.

Uses gpt-4o-mini with a single-token output for minimal latency and cost.
"""

from __future__ import annotations

import logging

import openai

log = logging.getLogger(__name__)

_GATE_SYSTEM = (
    "You are a speech relevance classifier for a voice-based tutoring app. "
    "The student is in a lesson and may be in a noisy environment.\n\n"
    "Given the transcribed utterance and the current teaching context, "
    "respond with EXACTLY one word:\n"
    "  respond — the student is speaking to the tutor (answering, asking, requesting)\n"
    "  ignore  — background noise, talking to someone else, mumbling, "
    "coughing, filler sounds, echoed tutor speech, or irrelevant chatter\n\n"
    "Examples of 'ignore': 'uh', 'hmm', 'yeah yeah hold on', 'hey can you grab that', "
    "'okay thanks bye', random single words, repetitions of what the tutor just said.\n"
    "Examples of 'respond': 'こんにちは', 'I think it means hello', 'can we try writing it', "
    "'yes', 'no', 'ready', 'sketchpad please', answers to questions.\n\n"
    "When in doubt, choose 'respond'. One word only."
)


async def classify_utterance(
    text: str,
    api_key: str,
    last_agent_text: str = "",
    model: str = "gpt-4o-mini",
    timeout_seconds: float = 3.0,
) -> str:
    """Classify an utterance as 'respond' or 'ignore'.

    Returns 'respond' on any error (fail-open so we never silently drop
    a real student answer).
    """
    if not text or not text.strip():
        return "ignore"

    user_msg = f"Utterance: \"{text}\""
    if last_agent_text:
        user_msg += f"\nTutor just said: \"{last_agent_text[:200]}\""

    try:
        client = openai.AsyncOpenAI(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=0,
        )
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=1,
            temperature=0,
            messages=[
                {"role": "system", "content": _GATE_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
        )
        result = (resp.choices[0].message.content or "").strip().lower()
        if result in ("respond", "ignore"):
            log.info("[utterance_gate] text=%r → %s", text[:80], result)
            return result
        # Unexpected output — fail open
        log.warning("[utterance_gate] unexpected result %r for %r, defaulting to respond", result, text[:80])
        return "respond"
    except Exception as exc:
        log.warning("[utterance_gate] classification failed (%s), defaulting to respond", exc)
        return "respond"
