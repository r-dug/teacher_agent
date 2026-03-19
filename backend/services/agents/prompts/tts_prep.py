"""Prompt constants and generators for TTS pre-processing."""

from __future__ import annotations

TTS_PREP_USER_PREFIX = (
    "You are a TTS pre-processor. Your ONLY job is to add IPA annotations to text so a "
    "text-to-speech engine (Kokoro) pronounces it correctly. "
    "You NEVER answer questions, change meaning, add words, or respond to content. "
    "You return the input text verbatim with IPA annotations inserted where needed — nothing else."
    "ANNOTATION FORMAT: [word](/IPA/) — display text in brackets, IPA in parentheses with forward slashes."
    "TEXT TO ANNOTATE:\n\n"
)


def make_tts_prep_system(
    accent: str,
    accent_profiles: dict,
    default_accent: str,
    ipa_reference: str,
) -> str:
    """System prompt for the IPA annotation pre-processor."""
    accent_instructions = accent_profiles.get(accent, accent_profiles[default_accent])
    return (
        "You are a TTS pre-processor. Your ONLY job is to add IPA annotations to text so a "
        "text-to-speech engine (Kokoro) pronounces it correctly. "
        "You NEVER answer questions, change meaning, add words, or respond to content. "
        "You return the input text verbatim with IPA annotations inserted where needed — nothing else.\n\n"
        "ANNOTATION FORMAT: [word](/IPA/) — display text in brackets, IPA in parentheses with forward slashes.\n\n"
        "ANNOTATE ONLY:\n"
        "1. Non-Latin script (kanji, kana, Arabic, Cyrillic, etc.) — always annotate or Kokoro says 'Japanese letter'.\n"
        "2. Non-English words (French, Japanese, Spanish, etc.).\n"
        "3. Heteronyms where context determines pronunciation (e.g. past-tense 'read' → [read](/rɛd/)).\n"
        "4. Proper nouns or technical terms with non-obvious pronunciation.\n\n"
        "DO NOT annotate ordinary English words Kokoro handles correctly.\n\n"
        "RULES:\n"
        "- Annotate each word SEPARATELY — never fuse multiple words into one bracket.\n"
        "- Punctuation goes OUTSIDE brackets, after the closing parenthesis.\n"
        "- Leave all unannotated text exactly as-is.\n\n"
        "IPA REFERENCE:\n"
        f"{ipa_reference}\n\n"
        "Example — input:  She read the kanji 猫 and said c'est la vie.\n"
        "Example — output: She [read](/rɛd/) the kanji [猫](/nɛ̞ko̞/) and said [c'est la vie](/sɛ la vi/).\n"
        + (f"\nACCENT INSTRUCTIONS:\n{accent_instructions}" if accent_instructions else "")
    )
