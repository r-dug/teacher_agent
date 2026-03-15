"""PDF text extraction helpers (plain PyMuPDF and Claude-powered)."""

from __future__ import annotations

import base64
from pathlib import Path

# ── Claude extraction ─────────────────────────────────────────────────────────

CLAUDE_MODEL = "claude-sonnet-4-6"

CLAUDE_PROMPTS: dict[str, str] = {
    "review": (
        "You are reviewing this document for an audio listener. "
        "Write a thorough, conversational review of each section. "
        "Your tone should be engaging and natural, as if explaining it to a student who you are teaching. "
        "Do not use any symbols, markdown, bullet points, numbered lists, or formatting. "
        "Write everything as flowing prose. "
        "Spell out all numbers, abbreviations, and acronyms the first time they appear. "
        "Avoid em-dashes; use commas or periods instead. "
        "Do not reference figures, tables, or citations by number; describe the ideas conveyed "
        "in the figures, tables, or citations if it enhances the audience's understanding."
    ),
    "doc_summary": (
        "Summarize this document for an audio listener. "
        "Write a clear, conversational summary covering the key points of each section. "
        "Use plain prose only — no bullet points, symbols, markdown, or numbered lists. "
        "Spell out numbers and abbreviations. Write as if speaking naturally aloud."
    ),
    "section_summary": (
        "Summarize this text for an audio listener. "
        "Write a clear, conversational summary covering the key points of each section. "
        "Use plain prose only — no bullet points, symbols, markdown, or numbered lists. "
        "Spell out numbers and abbreviations. Write as if speaking naturally aloud."
    ),
    "verbatim": (
        "Extract all readable text from this PDF and clean it up for text-to-speech. "
        "Remove headers, footers, page numbers, citations like [1] or [123], figure captions, "
        "and reference lists. "
        "Fix hyphenated line-breaks. Replace symbols with words. "
        "Spell out abbreviations the first time they appear. "
        "Output only clean, flowing prose ready to be read aloud."
    ),
}
DEFAULT_CLAUDE_MODE = "verbatim"


def extract_text_claude(pdf_path: str, mode: str = DEFAULT_CLAUDE_MODE) -> str:
    """Use the Claude API to read the PDF and produce TTS-ready text.

    Streams the response to stdout while returning the final text.
    """
    import anthropic

    system_prompt = CLAUDE_PROMPTS.get(mode, CLAUDE_PROMPTS[DEFAULT_CLAUDE_MODE])
    pdf_data = base64.standard_b64encode(Path(pdf_path).read_bytes()).decode("utf-8")
    client = anthropic.Anthropic()

    print(f"Sending PDF to Claude ({CLAUDE_MODEL}, mode={mode})...")
    with client.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=8192,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_data,
                        },
                    },
                    {"type": "text", "text": "Please process this document now."},
                ],
            }
        ],
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
        print()
        final = stream.get_final_message()

    return next(b.text for b in final.content if b.type == "text")


# ── PyMuPDF plain extraction ──────────────────────────────────────────────────

def extract_text_plain(pdf_path: str) -> str:
    """Extract plain text from all pages using PyMuPDF."""
    import pymupdf

    doc = pymupdf.open(pdf_path)
    pages = []
    for page in doc:
        text = page.get_text()
        if text.strip():
            pages.append(text)
    doc.close()
    return "\n\n".join(pages)
