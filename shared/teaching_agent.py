"""Teaching agent: curriculum decomposition, agentic teaching loop, TTS preparation."""

from __future__ import annotations

import base64
import json
import logging
import queue
import re
import threading
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

import numpy as np
import sounddevice as sd

from .constants import DEFAULT_KOKORO_VOICE, KOKORO_SAMPLE_RATE
from .lesson import Curriculum
from .phonetics import ACCENT_PROFILES, DEFAULT_ACCENT, IPA_REFERENCE, replace_roman_numerals

PDF_CHUNK_PAGES = 20
DEFAULT_LLM_MODEL = "claude-opus-4-6"


def _block_to_api_dict(block) -> dict:
    """Convert an Anthropic SDK content block to a plain dict the API accepts.

    model_dump() includes SDK-internal fields (e.g. parsed_output) that cause
    a 400 invalid_request_error on the next API call.  We keep only the fields
    that are part of the public API schema.
    """
    t = getattr(block, "type", None)
    if t == "text":
        return {"type": "text", "text": block.text}
    if t == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if t == "thinking":
        return {"type": "thinking", "thinking": block.thinking}
    # Fallback for unknown block types
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return dict(block)


# ── decomposition prompts ──────────────────────────────────────────────────────

DECOMPOSE_SYSTEM = (
    "You are an expert curriculum designer. Analyze the document and decompose it into "
    "logical teachable sections. Respond with a JSON object only — no explanation, no markdown."
)

DECOMPOSE_PROMPT = """\
Decompose this document into teachable sections and return JSON with this exact structure:

{
  "title": "Document title",
  "sections": [
    {
      "title": "Section title",
      "page_start": 3,
      "page_end": 7,
      "content": "Detailed content for teaching: preserve key definitions, arguments, examples, and data. Should be thorough enough for a teacher to explain from.",
      "key_concepts": ["specific testable concept", ...]
    }
  ]
}

Rules:
- Aim for 4-10 sections based on document length and natural structure.
- Each section should be a self-contained teaching unit.
- Key concepts (3-6 per section) must be specific and testable through dialogue.
- page_start and page_end are 1-based page numbers from the source document.
- Return only valid JSON."""

# ── teaching tools ─────────────────────────────────────────────────────────────

TEACHING_TOOLS = [
    {
        "name": "advance_to_next_section",
        "description": (
            "Call this ONLY after the student has answered at least one comprehension question "
            "and their answer demonstrates genuine understanding of the current section's key concepts. "
            "Do NOT call this preemptively or before asking any questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "evidence": {
                    "type": "string",
                    "description": "What the student said that demonstrates they understood.",
                }
            },
            "required": ["evidence"],
        },
    },
    {
        "name": "show_slide",
        "description": (
            "Display one or more pages from the PDF as a visual aid. "
            "Use this when a diagram, figure, table, or layout on a page would help the student. "
            "Use page_end to show a continuous range (e.g. a two-page spread or a sequence of diagrams). "
            "Provide a brief caption summarising what to focus on. "
            "The student can annotate any shown page and send it back to you."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "page_start": {
                    "type": "integer",
                    "description": "1-based first page to display.",
                },
                "page_end": {
                    "type": "integer",
                    "description": "1-based last page to display (inclusive). Omit or set equal to page_start for a single page.",
                },
                "caption": {
                    "type": "string",
                    "description": "One sentence describing what to focus on.",
                },
            },
            "required": ["page_start", "caption"],
        },
    },
    {
        "name": "open_sketchpad",
        "description": (
            "Open a drawing canvas for the student to practise writing characters, diagrams, "
            "or anything else that benefits from freehand input (e.g. Japanese kana, kanji, "
            "mathematical notation, diagrams). The canvas is returned to you as an image so "
            "you can evaluate what the student drew and give feedback. "
            "Optionally supply a background: text_bg for a faint reference character the "
            "student should trace or copy, or bg_page to show a PDF slide behind the canvas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Instruction shown above the canvas, e.g. 'Please write the character さ'.",
                },
                "text_bg": {
                    "type": "string",
                    "description": (
                        "Optional reference text to display as a faint guide behind the canvas "
                        "(e.g. 'さ', 'あ', '猫'). The student traces or copies it. "
                        "Use for character-writing practice."
                    ),
                },
                "bg_page": {
                    "type": "integer",
                    "description": (
                        "Optional 1-based PDF page number to display as a faint image behind "
                        "the canvas. Use when the student should annotate or reproduce a diagram."
                    ),
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "take_photo",
        "description": (
            "Ask the student to take a photo with their camera. Use this when you want to see "
            "something physical — their handwriting on paper, a real-world object, a physical "
            "diagram they drew, or their surroundings. The photo is returned as an image so you "
            "can observe, evaluate, or respond to what is shown."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Instruction shown to the student, e.g. 'Please show me what you wrote.'",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "mark_curriculum_complete",
        "description": (
            "Call this ONLY after the student has demonstrated thorough understanding of ALL "
            "sections, including the final section. Only valid after advance_to_next_section "
            "has been called for all preceding sections."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "evidence": {
                    "type": "string",
                    "description": "Summary of demonstrated understanding across the curriculum.",
                }
            },
            "required": ["evidence"],
        },
    },
]


def make_intro_prompt(title: str, sections: list[dict]) -> str:
    """System prompt for the one-time intro turn before teaching begins."""
    titles = [s.get("title", "") for s in sections if s.get("title")]
    outline = "; ".join(titles) if titles else "various topics"
    return (
        f'You are a warm, friendly teacher about to begin a lesson on "{title}" with a student '
        f"in a spoken voice conversation.\n\n"
        f"LESSON OUTLINE: {outline}\n\n"
        "YOUR TASK FOR THIS SINGLE TURN:\n"
        "1. Give a brief 2-3 sentence overview of what the lesson covers.\n"
        "2. Ask the student ONE question: what they hope to learn or get out of this lesson.\n\n"
        "Be concise and welcoming. Do NOT begin teaching yet — just orient and ask.\n\n"
        "VOICE RULES: Plain prose only. No markdown, bullets, or numbered lists. "
        "Spell out numbers and abbreviations. Avoid em-dashes."
    )


def make_teaching_prompt(title: str, sections: list[dict], idx: int, lesson_goal: str | None = None) -> str:
    total = len(sections)
    sec = sections[idx]
    covered = [s["title"] for s in sections[:idx]]
    covered_str = ", ".join(covered) if covered else "none yet"

    page_range = ""
    if sec.get("page_start") and sec.get("page_end"):
        page_range = f" (pages {sec['page_start']}–{sec['page_end']})"
    elif sec.get("page_start"):
        page_range = f" (page {sec['page_start']})"

    return (
        f'You are an expert, encouraging teacher working through "{title}" with a student '
        f"in a spoken voice conversation.\n\n"
        f"PROGRESS: Teaching section {idx + 1} of {total}. "
        f"Already covered: {covered_str}.\n\n"
        f"CURRENT SECTION — {sec['title']}{page_range}:\n{sec['content']}\n\n"
        f"KEY CONCEPTS TO VERIFY:\n"
        + "\n".join(f"- {c}" for c in sec["key_concepts"])
        + "\n\n"
        "APPROACH:\n"
        "1. Introduce the section briefly, then immediately engage the student with a question "
        "or exercise — don't lecture for more than 2-3 sentences before doing so.\n"
        "2. Cite the source when you introduce a fact or term (e.g. 'on page 4').\n"
        "3. Ask focused questions to probe understanding.\n"
        "4. When answers confirm genuine grasp of all key concepts, call "
        "advance_to_next_section (or mark_curriculum_complete if this is the final section).\n"
        "5. If understanding is incomplete, re-explain briefly from a different angle and ask again.\n\n"
        "CONCISENESS (CRITICAL):\n"
        "- Each response must be short: 2-4 sentences maximum before pausing with a question, "
        "exercise, or tool call. Do not deliver paragraphs of explanation.\n"
        "- Prefer doing over telling: a sketchpad exercise, slide, or question beats explaining.\n"
        "- Never repeat or restate what you just said. One sentence of feedback on a student "
        "answer, then immediately move on.\n"
        "- If you catch yourself writing a long response, cut it in half.\n\n"
        "TOOL USE — use tools liberally, they replace explanation:\n"
        "- show_slide: whenever a diagram, figure, or table exists, show it before explaining it.\n"
        "- open_sketchpad: have the student write characters, draw diagrams, or sketch concepts "
        "whenever active recall through writing would help. Use text_bg for reference characters.\n"
        "- take_photo: ask the student to show you physical work (handwriting on paper, etc.).\n"
        "Never skip a tool call to save time — exercises beat monologue every time.\n\n"
        "VOICE RULES: Plain prose only. No markdown, bullets, or numbered lists. "
        "Spell out numbers and abbreviations. Avoid em-dashes."
        + (
            f"\n\nSTUDENT'S LEARNING GOAL: {lesson_goal}\n"
            "Keep this goal in mind and tailor your teaching to help them achieve it."
            if lesson_goal else ""
        )
    )


# ── agent class ───────────────────────────────────────────────────────────────

class TeachingAgent:
    """
    Encapsulates the agentic teaching loop, TTS pipeline, and curriculum decomposition.

    All blocking operations (LLM calls, TTS synthesis, audio playback) are synchronous.
    Callers must invoke run_turn() and decompose_pdf() from background threads.

    Callbacks are fired from the calling (background) thread.  GUI callers should
    wrap them with root.after(0, ...) to safely update Tkinter widgets.
    """

    def __init__(
        self,
        llm_model: str,
        kokoro_pipeline=None,
        kokoro_voice: str = DEFAULT_KOKORO_VOICE,
        accent: str = DEFAULT_ACCENT,
        on_status: Callable[[str], None] | None = None,
        on_turn_start: Callable[[], None] | None = None,
        on_text_chunk: Callable[[str], None] | None = None,
        on_chunk_ready: Callable[[str, int, int], None] | None = None,
        on_audio_chunk: Callable[[np.ndarray, int, int], None] | None = None,
        on_show_slide: Callable[[int, int, str], None] | None = None,
        on_open_sketchpad: Callable[[str, list, threading.Event, str | None, int | None], None] | None = None,
        on_take_photo: Callable[[str, list, threading.Event], None] | None = None,
        on_section_advanced: Callable[[Curriculum], None] | None = None,
        on_curriculum_complete: Callable[[], None] | None = None,
        on_turn_complete: Callable[[np.ndarray | None], None] | None = None,
        on_response_end: Callable[[], None] | None = None,
        on_tts_playing: Callable[[bool], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ):
        self._llm_model = llm_model
        self.kokoro_pipeline = kokoro_pipeline
        self.kokoro_voice = kokoro_voice
        self.accent = accent

        self._on_status = on_status
        self._on_turn_start = on_turn_start
        self._on_text_chunk = on_text_chunk
        self._on_chunk_ready = on_chunk_ready
        self._on_audio_chunk = on_audio_chunk
        self._on_show_slide = on_show_slide
        self._on_open_sketchpad = on_open_sketchpad
        self._on_take_photo = on_take_photo
        self._on_section_advanced = on_section_advanced
        self._on_curriculum_complete = on_curriculum_complete
        self._on_turn_complete = on_turn_complete
        self._on_response_end = on_response_end
        self._on_tts_playing = on_tts_playing
        self._on_error = on_error

        self._audio_turns: list[list[np.ndarray]] = []
        self._audio_lock = threading.Lock()
        self.last_audio: np.ndarray | None = None

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def audio_turns(self) -> list[list[np.ndarray]]:
        return self._audio_turns

    def decompose_pdf(
        self,
        pdf_path: str,
        on_progress: Callable[[str], None] | None = None,
    ) -> Curriculum:
        """Decompose a PDF into a Curriculum. Synchronous; call from a background thread."""
        import anthropic
        import fitz  # pymupdf

        client = anthropic.Anthropic()
        doc = fitz.open(pdf_path)
        total_pages = len(doc)

        ranges = [
            (start, min(start + PDF_CHUNK_PAGES, total_pages))
            for start in range(0, total_pages, PDF_CHUNK_PAGES)
        ]
        n_chunks = len(ranges)

        doc_title: str | None = None
        all_sections: list[dict] = []

        for i, (start, end) in enumerate(ranges):
            if n_chunks > 1 and on_progress:
                on_progress(
                    f"Decomposing document... (pages {start + 1}–{end} of {total_pages})"
                )

            chunk_doc = fitz.open()
            chunk_doc.insert_pdf(doc, from_page=start, to_page=end - 1)
            pdf_bytes = chunk_doc.tobytes()
            chunk_doc.close()
            pdf_data = base64.standard_b64encode(pdf_bytes).decode("utf-8")

            chunk_note = (
                f"\nNote: This is pages {start + 1}–{end} of {total_pages} "
                f"(chunk {i + 1}/{n_chunks}). Extract sections only from these pages."
                if n_chunks > 1 else ""
            )

            supports_thinking = "haiku" not in self._llm_model
            response = client.messages.create(
                model=self._llm_model,
                max_tokens=8192,
                **({"thinking": {"type": "adaptive"}} if supports_thinking else {}),
                system=DECOMPOSE_SYSTEM,
                messages=[{
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
                        {"type": "text", "text": DECOMPOSE_PROMPT + chunk_note},
                    ],
                }],
            )

            raw = next(b.text for b in response.content if b.type == "text")
            raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
            data = json.loads(raw)

            if doc_title is None:
                doc_title = data["title"]
            all_sections.extend(data["sections"])

        doc.close()
        return Curriculum(title=doc_title or Path(pdf_path).stem, sections=all_sections)

    def generate_instructions(self, description: str) -> str:
        """Generate teacher persona instructions from a description. Synchronous."""
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=512,
            system=(
                "You are an expert at writing system prompts for AI tutors. "
                "Given a brief description of a desired teaching style or persona, "
                "write concise, actionable instructions (2-5 sentences) for how the "
                "teacher should behave: tone, questioning style, pacing, explanation "
                "approach. Always end with this mandatory paragraph: "
                "\"CRITICAL TTS RULE: This teacher speaks through a text-to-speech engine. "
                "Any non-Latin character (Japanese, Chinese, Arabic, Korean, Cyrillic, Greek, etc.) "
                "that appears bare in the output will be read aloud as 'japanese letter' or similar — "
                "which is unintelligible to the student. Every non-Latin character MUST appear inside "
                "square brackets followed by its IPA transcription in parentheses with surrounding "
                "forward slashes, like this: "
                "[さんねん](/sɑnnen/), [München](/mʏnçən/), [Bach](/bɑx/). "
                "NEVER write a non-Latin character outside of square brackets. No exceptions.\" "
                "Return only the instructions, no preamble or labels."
            ),
            messages=[{"role": "user", "content": f"Teaching style: {description}"}],
        )
        return response.content[0].text.strip()

    def prepare_for_tts(self, text: str) -> str:
        """Annotate text with IPA for Kokoro TTS. Synchronous."""
        text = re.sub(r"\*+", "", text)
        text = replace_roman_numerals(text)
        try:
            import anthropic as _anthropic
            client = _anthropic.Anthropic()
            response = client.messages.create(
                model="claude-opus-4-6",
                max_tokens=2048,
                system=self._tts_prep_system(),
                messages=[{"role": "user", "content": text}],
            )
            result = response.content[0].text.strip()
            result = re.sub(r'\[([^"\]]+)\]\((/[^/]*/)\)', r'["\1"](\2)', result)
            return result
        except Exception:
            return text

    def run_intro(self, curriculum: Curriculum, messages: list[dict]) -> None:
        """
        Run the one-time intro turn: overview + learning-goal question.
        Uses a minimal system prompt with no tools.  Synchronous.
        """
        if not messages:
            messages.append({"role": "user", "content": "Please begin."})
        self._do_single_llm_turn(
            curriculum, messages, agent_instructions=None,
            _system=make_intro_prompt(curriculum.title, curriculum.sections),
            _tools=[],
        )
        if self._on_turn_complete:
            self._on_turn_complete(self.last_audio)

    def run_turn(
        self,
        curriculum: Curriculum,
        messages: list[dict],
        agent_instructions: str | None,
        lesson_goal: str | None = None,
    ) -> None:
        """
        Run the agentic teaching loop.  May chain multiple LLM calls via tool use.
        Synchronous; call from a background thread.  Modifies messages in place.
        Fires callbacks throughout execution.
        """
        if not messages:
            messages.append({"role": "user", "content": "Please begin teaching."})

        while True:
            tool = self._do_single_llm_turn(curriculum, messages, agent_instructions, lesson_goal=lesson_goal)

            if tool is None:
                # No tool call — turn is done; enable recording.
                if self._on_turn_complete:
                    self._on_turn_complete(self.last_audio)
                return

            # Append a placeholder tool_result so the conversation stays valid.
            messages.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool.id, "content": "OK"}],
            })

            if tool.name == "advance_to_next_section":
                if curriculum.is_last:
                    if self._on_curriculum_complete:
                        self._on_curriculum_complete()
                    return
                curriculum.idx += 1
                if self._on_section_advanced:
                    self._on_section_advanced(curriculum)
                messages.clear()
                # Loop → next LLM call opens the new section.

            elif tool.name == "show_slide":
                page_start = tool.input.get("page_start", 1)
                page_end = tool.input.get("page_end", page_start)
                caption = tool.input.get("caption", "")
                if self._on_show_slide:
                    self._on_show_slide(page_start, page_end, caption)
                # Loop → continue teaching in the same section.

            elif tool.name == "open_sketchpad":
                prompt = tool.input.get("prompt", "Please draw:")
                text_bg: str | None = tool.input.get("text_bg")
                bg_page: int | None = tool.input.get("bg_page")
                result_holder: list[str | None] = [None]
                done_event = threading.Event()
                if self._on_open_sketchpad:
                    self._on_open_sketchpad(prompt, result_holder, done_event, text_bg, bg_page)
                done_event.wait()
                # Replace the placeholder with the actual image result.
                messages[-1] = {
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool.id,
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": result_holder[0],
                                },
                            },
                            {
                                "type": "text",
                                "text": (
                                    "The student has submitted their drawing. "
                                    "Please evaluate it and give feedback."
                                ),
                            },
                        ],
                    }],
                }
                # Loop → agent evaluates the sketch.

            elif tool.name == "take_photo":
                prompt = tool.input.get("prompt", "Please take a photo.")
                result_holder_p: list[str | None] = [None]
                done_event_p = threading.Event()
                if self._on_take_photo:
                    self._on_take_photo(prompt, result_holder_p, done_event_p)
                done_event_p.wait()
                messages[-1] = {
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool.id,
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": result_holder_p[0],
                                },
                            },
                            {
                                "type": "text",
                                "text": "The student has taken a photo. Please observe and respond.",
                            },
                        ],
                    }],
                }
                # Loop → agent sees the photo.

            elif tool.name == "mark_curriculum_complete":
                if self._on_curriculum_complete:
                    self._on_curriculum_complete()
                return

    def play_audio(self, audio: np.ndarray, stop_first: bool = False) -> None:
        """Play audio via the shared lock. Synchronous; blocks until playback ends."""
        if self._on_tts_playing:
            self._on_tts_playing(True)
        try:
            with self._audio_lock:
                if stop_first:
                    sd.stop()
                sd.play(audio, samplerate=KOKORO_SAMPLE_RATE)
                sd.wait()
        finally:
            if self._on_tts_playing:
                self._on_tts_playing(False)

    def clear_audio_turns(self) -> None:
        self._audio_turns.clear()

    # ── internals ─────────────────────────────────────────────────────────────

    def _tts_prep_system(self) -> str:
        accent_name = self.accent
        accent_instructions = ACCENT_PROFILES.get(accent_name, ACCENT_PROFILES[DEFAULT_ACCENT])
        return (
            "You are a phonetic transcription pre-processor for a text-to-speech engine (Kokoro). "
            "Kokoro handles common English words well on its own. Your job is to annotate ONLY the "
            "words and characters it is likely to mispronounce, using the format [word](/IPA/) — "
            "where word is the display text and /IPA/ is the IPA transcription enclosed in forward slashes.\n\n"
            f"ACCENT: {accent_name}\n"
            f"{accent_instructions}\n\n"
            "ANNOTATE (with [word](/IPA/)) only these categories:\n"
            "1. Non-English words and phrases (French, Japanese, Spanish, etc.).\n"
            "2. Non-Latin script characters (kanji, kana, Arabic, Cyrillic, etc.) — ALWAYS wrap these; "
            "Kokoro will say 'Japanese letter' otherwise. Example: [猫](/nɛ̞ko̞/).\n"
            "3. Heteronyms whose pronunciation depends on context "
            "(e.g. 'read' past-tense → [read](/rɛd/), 'wind' the verb → [wind](/waɪnd/)).\n"
            "4. Unusual proper nouns or technical terms with non-obvious pronunciation.\n"
            "5. Already-formatted [word](/IPA/) pairs — re-transcribe the IPA for the selected accent.\n\n"
            "DO NOT annotate ordinary English words that Kokoro handles correctly "
            "(e.g. 'the', 'cat', 'letters', 'hello').\n\n"
            "RULES:\n"
            "- Annotate each word or morpheme SEPARATELY. Never fuse multiple words or a name+particle "
            "into one bracket. For example, きむら たけし です must become "
            "[きむら](/kimuɾa/) [たけし](/tɑkeɕi/) [です](/desu/), NOT [きむらたけしです](/.../).\n"
            "- A bare [word] in the input that is NOT immediately followed by (/IPA/) is NOT a Kokoro "
            "annotation — it is plain bracketed text. "
            "Strip the brackets and annotate the content normally if needed.\n"
            "- Convert Roman numerals to Arabic numerals (e.g. Chapter IV → Chapter 4, Part III → Part 3).\n"
            "- Leave unannotated text exactly as-is (no changes to spelling or spacing).\n"
            "- Punctuation stays OUTSIDE the brackets, immediately after the closing parenthesis.\n"
            "- Do NOT add any explanation, preamble, or commentary.\n"
            "- Output ONLY the processed text.\n\n"
            "IPA SYMBOL REFERENCE (use only valid IPA from this list):\n"
            f"{IPA_REFERENCE}\n\n"
            "Example input:  She read the kanji 猫 and said 'c'est la vie'. [Name]です.\n"
            "Example output: She [read](/rɛd/) the kanji [猫](/nɛ̞ko̞/) and said "
            "'[c'est la vie](/sɛ la vi/)'. Name[です](/desu/).\n"
        )

    def _do_single_llm_turn(
        self,
        curriculum: Curriculum,
        messages: list[dict],
        agent_instructions: str | None,
        lesson_goal: str | None = None,
        _system: str | None = None,
        _tools: list | None = None,
    ):
        """
        Run one LLM streaming call, pipeline TTS synthesis and playback in parallel.
        Appends the assistant message to messages.
        Returns the first tool_use block, or None if no tool was called.
        """
        import anthropic

        client = anthropic.Anthropic()
        if _system is not None:
            system = _system
        else:
            system = make_teaching_prompt(
                curriculum.title, curriculum.sections, curriculum.idx, lesson_goal
            )
            if agent_instructions:
                system += f"\n\nADDITIONAL STYLE INSTRUCTIONS:\n{agent_instructions}"
        tools = TEACHING_TOOLS if _tools is None else _tools

        tts_queue: queue.Queue = queue.Queue()
        audio_queue: queue.Queue = queue.Queue()
        all_audio: list[np.ndarray] = []

        turn_idx = len(self._audio_turns)
        turn_chunks: list[np.ndarray] = []
        self._audio_turns.append(turn_chunks)
        chunk_counter = [0]

        def _flush(text: str) -> None:
            chunk_idx = chunk_counter[0]
            chunk_counter[0] += 1
            tag = f"t{turn_idx}c{chunk_idx}"
            if self._on_chunk_ready:
                self._on_chunk_ready(tag, turn_idx, chunk_idx)
            tts_queue.put(text)

        _STOP = object()

        def _tts_worker() -> None:
            while True:
                text = tts_queue.get()
                if text is None:
                    audio_queue.put(_STOP)
                    return
                try:
                    spoken = self.prepare_for_tts(text)
                    chunks = [
                        np.clip(audio.numpy(), -1.0, 1.0)
                        for _, _, audio in self.kokoro_pipeline(spoken, voice=self.kokoro_voice)
                    ]
                    audio_queue.put(
                        np.concatenate(chunks).astype(np.float32) if chunks
                        else np.zeros(0, dtype=np.float32)
                    )
                except Exception as e:
                    if self._on_error:
                        self._on_error(str(e))
                    audio_queue.put(_STOP)
                    return

        def _audio_player() -> None:
            _chunk_idx = 0
            while True:
                audio_data = audio_queue.get()
                if audio_data is _STOP:
                    return
                turn_chunks.append(audio_data)
                if audio_data.size > 0:
                    all_audio.append(audio_data)
                    try:
                        if self._on_tts_playing:
                            self._on_tts_playing(True)
                        if self._on_audio_chunk:
                            # Backend mode: stream chunk to caller instead of local playback.
                            self._on_audio_chunk(audio_data, turn_idx, _chunk_idx)
                        else:
                            with self._audio_lock:
                                sd.play(audio_data, samplerate=KOKORO_SAMPLE_RATE)
                                sd.wait()
                    except Exception as e:
                        if self._on_error:
                            self._on_error(str(e))
                        return
                _chunk_idx += 1

        tts_thread = threading.Thread(target=_tts_worker, daemon=True)
        audio_thread = threading.Thread(target=_audio_player, daemon=True)
        tts_thread.start()
        audio_thread.start()

        full_text = ""
        tool_use_block = None
        text_buf = ""

        try:
            log.info("_do_single_llm_turn: opening stream (model=%s, messages=%d)", self._llm_model, len(messages))
            stream_kwargs: dict = dict(
                model=self._llm_model,
                max_tokens=2048,
                system=system,
                messages=messages,
            )
            if tools:
                stream_kwargs["tools"] = tools
            with client.messages.stream(**stream_kwargs) as stream:
                log.info("_do_single_llm_turn: stream opened, firing turn_start")
                if self._on_turn_start:
                    self._on_turn_start()
                for chunk in stream.text_stream:
                    full_text += chunk
                    text_buf += chunk
                    if self._on_text_chunk:
                        self._on_text_chunk(chunk)

                    if "\n" in text_buf:
                        parts = text_buf.split("\n")
                        for part in parts[:-1]:
                            if part.strip():
                                _flush(part.strip())
                        text_buf = parts[-1]
                    elif len(text_buf.split()) >= 400:
                        _flush(text_buf.strip())
                        text_buf = ""

                final = stream.get_final_message()

            # Convert SDK content blocks to plain dicts with only the fields the API
            # accepts.  model_dump() includes SDK-internal fields like parsed_output
            # that cause a 400 on the next call.
            messages.append({"role": "assistant", "content": [
                _block_to_api_dict(b) for b in final.content
            ]})

            for block in final.content:
                if block.type == "tool_use":
                    tool_use_block = block
                    break

        except Exception as e:
            log.exception("_do_single_llm_turn: LLM/TTS exception: %s", e)
            if self._on_error:
                self._on_error(str(e))
            tts_queue.put(None)
            audio_thread.join()
            return None

        if text_buf.strip():
            _flush(text_buf.strip())
        tts_queue.put(None)
        audio_thread.join()

        if all_audio:
            self.last_audio = np.concatenate(all_audio)

        if self._on_response_end:
            self._on_response_end()

        return tool_use_block
