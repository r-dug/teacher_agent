#!/usr/bin/env python3
"""Convert a PDF to an audio file using Piper TTS or Kokoro TTS."""

import argparse
import sys
from pathlib import Path

from shared.constants import KOKORO_VOICES, DEFAULT_KOKORO_VOICE, PIPER_VOICES, DEFAULT_PIPER_VOICE
from shared.pdf_tools import (
    extract_text_claude,
    extract_text_plain,
    CLAUDE_PROMPTS,
    DEFAULT_CLAUDE_MODE,
)
from shared.tts import synthesize_kokoro, synthesize_piper, resolve_piper_model


def main():
    parser = argparse.ArgumentParser(description="Convert PDF to audio using Piper or Kokoro TTS")
    parser.add_argument("pdf", nargs="?", help="Input PDF file")
    parser.add_argument("-o", "--output", help="Output file (.wav or .mp3). Default: <pdf>.wav")
    parser.add_argument(
        "-e", "--engine", default="kokoro", choices=["piper", "kokoro"],
        help="TTS engine (default: kokoro)",
    )
    parser.add_argument(
        "-v", "--voice", default=None,
        help=f"Voice (piper default: {DEFAULT_PIPER_VOICE}, kokoro default: {DEFAULT_KOKORO_VOICE})",
    )
    parser.add_argument(
        "--model", help="Piper only: override with a raw model name, e.g. en_US-ryan-high",
    )
    parser.add_argument("--list-voices", action="store_true", help="List available voices and exit")
    parser.add_argument("--text-only", action="store_true", help="Only extract text, skip synthesis")
    parser.add_argument(
        "--claude", action="store_true",
        help="Use Claude API to extract and rewrite text for TTS (requires ANTHROPIC_API_KEY)",
    )
    parser.add_argument(
        "--claude-mode", default=DEFAULT_CLAUDE_MODE, choices=list(CLAUDE_PROMPTS.keys()),
        help=(
            f"Claude rewrite style (default: {DEFAULT_CLAUDE_MODE}). "
            "'verbatim'=clean extraction, 'summary'=condensed, 'review'=section-by-section commentary"
        ),
    )
    args = parser.parse_args()

    if args.list_voices:
        print("Piper voices (--engine piper --voice NAME):")
        for k, v in PIPER_VOICES.items():
            marker = " (default)" if k == DEFAULT_PIPER_VOICE else ""
            print(f"  {k:20s}  {v}{marker}")
        print("\nKokoro voices (--engine kokoro --voice NAME):")
        for k in KOKORO_VOICES:
            marker = " (default)" if k == DEFAULT_KOKORO_VOICE else ""
            print(f"  {k}{marker}")
        print("\nMore piper voices: https://huggingface.co/rhasspy/piper-voices")
        return

    if not args.pdf:
        parser.print_help()
        sys.exit(1)

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"Error: {pdf_path} not found", file=sys.stderr)
        sys.exit(1)

    if args.claude:
        text = extract_text_claude(str(pdf_path), mode=args.claude_mode)
    else:
        print(f"Extracting text from {pdf_path}...")
        text = extract_text_plain(str(pdf_path))

    word_count = len(text.split())
    print(f"Extracted ~{word_count} words")

    if args.text_only:
        txt_out = pdf_path.with_suffix(".txt")
        txt_out.write_text(text, encoding="utf-8")
        print(f"Text saved to {txt_out}")
        return

    if args.engine == "kokoro":
        voice = args.voice or DEFAULT_KOKORO_VOICE
    else:
        voice = args.voice or DEFAULT_PIPER_VOICE

    if args.output:
        output = args.output
    else:
        out_dir = Path("out")
        out_dir.mkdir(exist_ok=True)
        output = str(out_dir / f"{pdf_path.stem}_{args.engine}_{voice}.wav")

    if args.engine == "kokoro":
        synthesize_kokoro(text, output, voice)
    else:
        model_name = resolve_piper_model(voice, args.model)
        synthesize_piper(text, output, model_name)


if __name__ == "__main__":
    main()
