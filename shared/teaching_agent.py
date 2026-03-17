"""Teaching agent: curriculum decomposition, agentic teaching loop, TTS preparation."""

from __future__ import annotations

import base64
import json
import logging
import queue
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

log = logging.getLogger(__name__)

import numpy as np
import sounddevice as sd

from .constants import DEFAULT_KOKORO_VOICE, KOKORO_SAMPLE_RATE
from .lesson import Curriculum
from .phonetics import ACCENT_PROFILES, DEFAULT_ACCENT, IPA_REFERENCE, replace_roman_numerals

SEGMENT_TARGET_PAGES = 25   # ideal page count per parallel analysis segment
MAX_SEGMENT_WORKERS = 8     # max concurrent LLM calls in Phase 2
_HAIKU_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_LLM_MODEL = "claude-sonnet-4-6"


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
        d: dict = {"type": "thinking", "thinking": block.thinking}
        # signature is required when passing thinking blocks back to the API
        if getattr(block, "signature", None):
            d["signature"] = block.signature
        return d
    # Fallback for unknown block types
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return dict(block)


def _strip_dangling_tool_use(messages: list[dict]) -> None:
    """
    Remove assistant messages whose tool_use blocks have no matching tool_result
    in the immediately following message.

    Scans the entire list (not just the tail) because the disconnect/save race can
    leave the conversation in various partially-written states.  After removing an
    unmatched assistant message, the orphaned user(tool_result) that may follow it
    is also removed to keep the list in a valid alternating-role state.
    """
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                tool_ids = {
                    b.get("id") for b in content
                    if isinstance(b, dict) and b.get("type") == "tool_use"
                }
                if tool_ids:
                    # Check whether the very next message supplies tool_results for all IDs.
                    next_msg = messages[i + 1] if i + 1 < len(messages) else None
                    next_content = next_msg.get("content", []) if next_msg else []
                    result_ids = {
                        b.get("tool_use_id") for b in (next_content if isinstance(next_content, list) else [])
                        if isinstance(b, dict) and b.get("type") == "tool_result"
                    }
                    if not tool_ids.issubset(result_ids):
                        # Unmatched tool_use — remove this assistant message.
                        messages.pop(i)
                        # Also remove the next message if it's a user(tool_result) — it's orphaned.
                        if i < len(messages):
                            nm = messages[i]
                            nc = nm.get("content", [])
                            if (
                                nm.get("role") == "user"
                                and isinstance(nc, list)
                                and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in nc)
                            ):
                                messages.pop(i)
                        continue  # re-check at same index
        i += 1

    # Pass 2: remove user messages whose tool_result blocks have no matching
    # tool_use in the immediately preceding assistant message.
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "user":
            content = msg.get("content", [])
            if isinstance(content, list):
                result_ids = {
                    b.get("tool_use_id") for b in content
                    if isinstance(b, dict) and b.get("type") == "tool_result"
                }
                if result_ids:
                    prev_msg = messages[i - 1] if i > 0 else None
                    prev_content = prev_msg.get("content", []) if prev_msg else []
                    tool_ids = {
                        b.get("id") for b in (prev_content if isinstance(prev_content, list) else [])
                        if isinstance(b, dict) and b.get("type") == "tool_use"
                    }
                    if not result_ids.issubset(tool_ids):
                        messages.pop(i)
                        continue
        i += 1


def _tool_schema_to_openai(tool: dict) -> dict:
    """Convert Anthropic-style tool schema to OpenAI Chat Completions format."""
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
        },
    }


def _tool_result_content_to_text_and_images(content) -> tuple[str, list[dict]]:
    """
    Convert Anthropic tool_result content to a tool text payload plus optional
    user multimodal blocks (for image-bearing submissions).
    """
    if isinstance(content, str):
        text = content.strip()
        return (text if text else "OK"), []

    if not isinstance(content, list):
        return "OK", []

    text_parts: list[str] = []
    user_blocks: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            txt = (block.get("text") or "").strip()
            if txt:
                text_parts.append(txt)
        elif btype == "image":
            src = block.get("source") or {}
            if src.get("type") == "base64":
                media_type = src.get("media_type", "image/png")
                data = src.get("data", "")
                if data:
                    user_blocks.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{data}"},
                    })

    tool_text = "\n".join(text_parts).strip()
    if not tool_text and user_blocks:
        tool_text = f"Student submitted {len(user_blocks)} image(s)."
    if not tool_text:
        tool_text = "OK"

    if user_blocks:
        user_blocks.insert(0, {"type": "text", "text": "Tool result images from the student."})
    return tool_text, user_blocks


def _messages_to_openai(messages: list[dict]) -> list[dict]:
    """
    Convert internal Anthropic-style message history to OpenAI chat messages.

    This preserves tool-call chains and also forwards image-bearing tool results
    as follow-up user multimodal messages.
    """
    out: list[dict] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if not isinstance(content, list):
            out.append({"role": role, "content": str(content)})
            continue

        if role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    txt = (block.get("text") or "").strip()
                    if txt:
                        text_parts.append(txt)
                elif btype == "tool_use":
                    try:
                        args_json = json.dumps(block.get("input") or {})
                    except Exception:
                        args_json = "{}"
                    tool_calls.append({
                        "id": block.get("id") or "",
                        "type": "function",
                        "function": {
                            "name": block.get("name") or "",
                            "arguments": args_json,
                        },
                    })

            if text_parts or tool_calls:
                msg_out: dict = {
                    "role": "assistant",
                    "content": "\n".join(text_parts).strip() if text_parts else None,
                }
                if tool_calls:
                    msg_out["tool_calls"] = tool_calls
                out.append(msg_out)
            continue

        if role == "user":
            plain_text_parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    txt = (block.get("text") or "").strip()
                    if txt:
                        plain_text_parts.append(txt)
                elif btype == "tool_result":
                    tool_call_id = block.get("tool_use_id") or ""
                    tool_text, user_blocks = _tool_result_content_to_text_and_images(
                        block.get("content")
                    )
                    out.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": tool_text,
                    })
                    if user_blocks:
                        out.append({"role": "user", "content": user_blocks})

            if plain_text_parts:
                out.append({"role": "user", "content": "\n".join(plain_text_parts).strip()})
            continue

        # Fallback for unexpected roles
        out.append({"role": "user", "content": json.dumps(content)})

    return out


def _format_openai_chat_error(response) -> str:
    """Best-effort extraction of OpenAI chat API error details."""
    try:
        data = response.json()
    except Exception:
        text = getattr(response, "text", "")
        return f"{getattr(response, 'status_code', 'error')} {text[:300]}"
    message = (data.get("error") or {}).get("message")
    if message:
        return f"{getattr(response, 'status_code', 'error')} {message}"
    return f"{getattr(response, 'status_code', 'error')} {str(data)[:300]}"


# ── intro goal-gathering tool ──────────────────────────────────────────────────

CAPTURE_GOAL_TOOL = {
    "name": "capture_lesson_goal",
    "description": (
        "Call this once you clearly understand what the student wants to learn from this lesson. "
        "Ends goal-gathering and begins tailored lesson preparation."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "Clear, concise summary of the student's learning goal (1-3 sentences).",
            },
            "depth": {
                "type": "string",
                "enum": ["beginner", "intermediate", "advanced"],
                "description": "Inferred expertise level of the student for this topic.",
            },
        },
        "required": ["goal"],
    },
}

# ── decomposition search tools ─────────────────────────────────────────────────

SEARCH_WEB_TOOL = {
    "name": "search_web",
    "description": (
        "Search the web for factual information when the document is ambiguous, uses uncommon "
        "terminology, or covers a topic that needs supplementary context for lesson planning. "
        "Use sparingly — only when the document text alone is clearly insufficient."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Specific search query to clarify ambiguous terms or concepts.",
            },
            "reason": {
                "type": "string",
                "description": "Why this search is needed for the decomposition.",
            },
        },
        "required": ["query", "reason"],
    },
}

SEARCH_GUARDRAIL_SYSTEM = (
    "You are a research assistant. Your ONLY task is to find factual, reliable information "
    "from trustworthy sources to help plan a lesson.\n\n"
    "ALLOWED SOURCES:\n"
    "- Wikipedia and encyclopedias\n"
    "- Official government and institutional websites (.gov, .edu, .org)\n"
    "- Peer-reviewed academic sources and preprints (arXiv, PubMed, etc.)\n"
    "- Official language/standards/specification documentation\n"
    "- Major established reference publishers (Britannica, etc.)\n"
    "- Major established news organisations (BBC, Reuters, AP)\n\n"
    "FORBIDDEN SOURCES: personal blogs, Reddit/forums, social media, commercial product "
    "pages, SEO content farms, anonymous or unverifiable sources.\n\n"
    "Return a concise factual summary (3-6 sentences) based only on trustworthy sources. "
    "If no reliable sources are found, say so explicitly. No opinions or recommendations."
)

# ── decomposition prompts ──────────────────────────────────────────────────────

DECOMPOSE_SYSTEM = (
    "You are an expert curriculum designer. Analyze the document and decompose it into "
    "logical teachable sections. Respond with a JSON object only — no explanation, no markdown. "
    "CRITICAL: page_start and page_end must be the exact page numbers where each section "
    "appears in the PDF. Count pages carefully — these numbers are used to display the actual "
    "PDF pages to the student during teaching."
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
- page_start and page_end are 1-based page numbers from the PDF. They must be exact:
  if a section starts on page 3 and ends on page 7, use page_start=3, page_end=7.
  If a section occupies only one page, set both equal (e.g. page_start=5, page_end=5).
  Do not guess — locate each section in the document before assigning page numbers.
- Return only valid JSON."""

# ── segmentation prompts (Phase 1 — cheap, text-only) ─────────────────────────

_SEGMENT_SYSTEM = (
    "You are a document structure analyst. Identify natural topic boundaries in "
    "a document based on its page-by-page content summary. "
    "Respond with JSON only — no explanation, no markdown."
)

_SEGMENT_PROMPT_TEMPLATE = """\
Below is the first line of text from each page of a {total_pages}-page document.

{structural_text}

Identify natural topic or chapter boundaries and divide the document into segments \
of {min_pages}–{max_pages} pages each. Boundaries should fall at clear topic transitions.

Return JSON:
{{"title": "Document title (inferred from content)", "segments": [{{"page_start": 1, "page_end": {example_end}}}, ...]}}

Rules:
- page_start and page_end are 1-based and inclusive.
- Aim for ~{target} pages per segment; merge short sections, split very long ones.
- Cover the entire document with no gaps.
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
        "name": "record_video",
        "description": (
            "Ask the student to record a short video with their camera. Use this when you need "
            "to observe motion or a sequence of actions — for example, signing in ASL, "
            "demonstrating a physical technique, or showing a process step by step. "
            "The video is sampled into frames which you can observe and evaluate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Instruction shown to the student, e.g. 'Please sign the word HELLO in ASL.'",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "open_code_editor",
        "description": (
            "Open an interactive code editor for the student to complete a coding challenge. "
            "The student writes code, runs it to see output, and submits when satisfied. "
            "You receive both the final code and its execution output. "
            "Use for any exercise requiring the student to write, debug, or modify code. "
            "Use python-ml for exercises involving NumPy, Pandas, scikit-learn, matplotlib, or PyTorch."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The coding challenge or instructions shown above the editor.",
                },
                "language": {
                    "type": "string",
                    "enum": ["python", "python-ml", "javascript", "typescript", "c", "cpp", "rust"],
                    "description": "Programming language / runtime environment.",
                },
                "starter_code": {
                    "type": "string",
                    "description": (
                        "Optional code pre-filled in the editor. "
                        "Use for bug-fixing exercises or to provide a function scaffold."
                    ),
                },
            },
            "required": ["prompt", "language"],
        },
    },
    {
        "name": "open_html_editor",
        "description": (
            "Open a dual HTML + CSS editor with a live iframe preview. "
            "Use for web development exercises where the student writes or modifies HTML and CSS. "
            "The student clicks Run to update the preview, then submits. "
            "You receive the final HTML and CSS."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The exercise instructions shown above the editor.",
                },
                "starter_html": {
                    "type": "string",
                    "description": "Optional HTML starter code.",
                },
                "starter_css": {
                    "type": "string",
                    "description": "Optional CSS starter code.",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "start_timer",
        "description": (
            "Give the student a timed exercise. A countdown timer is displayed on screen. "
            "The student can type an answer and submit early, or wait for time to expire. "
            "You receive whether time expired, how long they took, and their written answer. "
            "Use for recall drills, timed translation challenges, vocabulary sprints, or any "
            "exercise where time pressure reinforces fluency."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The exercise instruction shown above the timer, e.g. 'Write the hiragana for all five vowels.'",
                },
                "duration_seconds": {
                    "type": "integer",
                    "description": "How long the student has, in seconds (e.g. 30, 60, 120).",
                },
            },
            "required": ["prompt", "duration_seconds"],
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


def make_intro_prompt(title: str, sections: list[dict], raw_text: str | None = None) -> str:
    """System prompt for the agentic goal-gathering intro loop.

    The agent may exchange up to 3 messages with the student before calling
    capture_lesson_goal.  When sections are not yet available (deferred
    decomposition), raw_text from a cheap PyMuPDF extraction is used so the
    agent can give a meaningful overview.
    """
    if sections:
        titles = [s.get("title", "") for s in sections if s.get("title")]
        doc_context = f"LESSON OUTLINE: {'; '.join(titles)}"
    elif raw_text:
        preview = raw_text[:2000].strip()
        doc_context = f"DOCUMENT PREVIEW (first pages — full analysis pending):\n{preview}"
    else:
        doc_context = f'DOCUMENT: "{title}" (content will be fully analysed shortly)'

    return (
        f'You are a warm, friendly teacher about to begin a lesson on "{title}" with a student '
        f"in a spoken voice conversation.\n\n"
        f"{doc_context}\n\n"
        "YOUR ROLE: Before teaching begins, understand the student's learning goals so the "
        "lesson can be tailored specifically for them.\n\n"
        "PROCESS (at most 3 exchanges total):\n"
        "1. FIRST exchange: give a brief 1-2 sentence overview of the lesson, then ask what "
        "the student hopes to learn or achieve.\n"
        "2. If their response is clear and specific: call capture_lesson_goal immediately.\n"
        "3. If vague or you need to know their experience level: ask exactly ONE follow-up.\n"
        "4. After the follow-up, call capture_lesson_goal regardless — do not keep asking.\n\n"
        "Do NOT begin teaching yet. Ask at most 2 questions total. Be concise and welcoming.\n\n"
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
        "- start_timer: give timed recall drills or translation sprints to build fluency under pressure.\n"
        "Never skip a tool call to save time — exercises beat monologue every time.\n\n"
        "VOICE RULES: Plain prose only. No markdown, bullets, or numbered lists. "
        "Spell out numbers and abbreviations. Avoid em-dashes."
        + (
            f"\n\nSTUDENT'S LEARNING GOAL: {lesson_goal}\n"
            "Keep this goal in mind and tailor your teaching to help them achieve it."
            if lesson_goal else ""
        )
    )


# ── Phase 1 segmentation helpers ──────────────────────────────────────────────

def _extract_structural_text(doc) -> str:
    """Return the first text line of each page, prefixed with its 1-based page number.

    This compact representation (~130 chars × n_pages) is enough for the
    segmentation LLM to locate chapter/topic boundaries without sending the
    full document text.
    """
    lines: list[str] = []
    for page_num, page in enumerate(doc, start=1):
        for block in page.get_text("blocks"):
            text = block[4].strip().replace("\n", " ")
            if text:
                lines.append(f"p{page_num}: {text[:120]}")
                break
    return "\n".join(lines)


def _toc_segments(doc, total_pages: int, target: int) -> list[tuple[int, int]] | None:
    """Derive 0-based (start, end_exclusive) segments from the PDF's embedded TOC.

    Uses level-1 entries (chapters) as natural boundaries.  Returns None when:
    - The TOC is absent or has fewer than 2 level-1 entries.
    - Any single segment exceeds 3× the target (TOC is too coarse to help).
    - Any segment is fewer than 5 pages (TOC is too granular; merging needed).
    """
    toc = doc.get_toc()  # [(level, title, page_1based), ...]
    if not toc:
        return None

    level1_pages = sorted({page - 1 for level, _, page in toc if level == 1})
    if len(level1_pages) < 2:
        return None

    segments = [
        (level1_pages[i], level1_pages[i + 1] if i + 1 < len(level1_pages) else total_pages)
        for i in range(len(level1_pages))
    ]

    if any((end - start) > target * 3 for start, end in segments):
        return None  # too coarse — fall through to LLM
    if any((end - start) < 5 for start, end in segments):
        return None  # too granular — fall through to LLM

    return segments


def _find_segments(
    doc,
    client,
    total_pages: int,
) -> tuple[list[tuple[int, int]], str | None]:
    """Phase 1: find natural segment boundaries using (in priority order):
      1. Embedded PDF TOC (free, zero LLM calls)
      2. Cheap LLM call on structural text (Haiku, text-only, ~1 k tokens)
      3. Fixed-size fallback (SEGMENT_TARGET_PAGES per chunk)

    Returns (segments, doc_title) where each segment is a
    (start_0indexed, end_exclusive_0indexed) pair covering the whole document.
    doc_title is the LLM's inferred title, or None if from TOC/fallback.
    """
    import anthropic as _anthropic

    # 1. Try TOC (free)
    toc_segs = _toc_segments(doc, total_pages, SEGMENT_TARGET_PAGES)
    if toc_segs:
        return toc_segs, None

    # 2. LLM on structural text
    structural = _extract_structural_text(doc)
    min_p = max(10, SEGMENT_TARGET_PAGES // 2)
    max_p = SEGMENT_TARGET_PAGES * 2
    prompt = _SEGMENT_PROMPT_TEMPLATE.format(
        total_pages=total_pages,
        structural_text=structural,
        min_pages=min_p,
        max_pages=max_p,
        target=SEGMENT_TARGET_PAGES,
        example_end=min(SEGMENT_TARGET_PAGES, total_pages),
    )

    doc_title: str | None = None
    try:
        resp = _anthropic.Anthropic(max_retries=3).messages.create(
            model=_HAIKU_MODEL,
            max_tokens=1024,
            system=_SEGMENT_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
        raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
        raw = raw[raw.find("{") : raw.rfind("}") + 1]
        data = json.loads(raw)
        doc_title = data.get("title")

        segs: list[tuple[int, int]] = []
        for s in data.get("segments", []):
            # 1-based inclusive → 0-based (start, end_exclusive)
            start = max(1, int(s["page_start"])) - 1
            end   = min(total_pages, int(s["page_end"]))  # 1-based end == 0-based exclusive
            if start < end:
                segs.append((start, end))

        if segs:
            return segs, doc_title

    except Exception as exc:
        log.warning("Segment LLM call failed, falling back to fixed chunks: %s", exc)

    # 3. Fixed-size fallback
    return [
        (start, min(start + SEGMENT_TARGET_PAGES, total_pages))
        for start in range(0, total_pages, SEGMENT_TARGET_PAGES)
    ], doc_title


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
        teach_llm_provider: str = "anthropic",
        teach_llm_model: str | None = None,
        decompose_llm_provider: str | None = None,
        decompose_llm_model: str | None = None,
        openai_api_key: str | None = None,
        openai_timeout_seconds: float = 30.0,
        openai_max_retries: int = 1,
        openai_decompose_timeout_seconds: float | None = None,
        openai_decompose_max_retries: int | None = None,
        openai_decompose_max_input_chars: int = 120000,
        tts_provider=None,
        fallback_tts_provider=None,
        tts_voice: str | None = None,
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
        on_record_video: Callable[[str, list, threading.Event], None] | None = None,
        on_open_code_editor: Callable[[str, str, str | None, list, threading.Event], None] | None = None,
        on_open_html_editor: Callable[[str, str | None, str | None, list, threading.Event], None] | None = None,
        on_start_timer: Callable[[str, int, list, threading.Event], None] | None = None,
        on_token_usage: Callable[[str, str, object], None] | None = None,
        on_section_advanced: Callable[[Curriculum], None] | None = None,
        on_curriculum_complete: Callable[[], None] | None = None,
        on_turn_complete: Callable[[np.ndarray | None], None] | None = None,
        on_response_end: Callable[[], None] | None = None,
        on_tts_playing: Callable[[bool], None] | None = None,
        on_tts_done: Callable[..., None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ):
        self._llm_model = llm_model
        self._teach_llm_provider = (teach_llm_provider or "anthropic").strip().lower()
        self._teach_llm_model = teach_llm_model or llm_model
        self._decompose_llm_provider = (decompose_llm_provider or self._teach_llm_provider or "anthropic").strip().lower()
        self._decompose_llm_model = decompose_llm_model or llm_model
        self._openai_api_key = (openai_api_key or "").strip()
        self._openai_timeout_seconds = max(1.0, float(openai_timeout_seconds))
        self._openai_max_retries = max(0, int(openai_max_retries))
        self._openai_decompose_timeout_seconds = max(
            1.0,
            float(
                openai_decompose_timeout_seconds
                if openai_decompose_timeout_seconds is not None
                else self._openai_timeout_seconds
            ),
        )
        self._openai_decompose_max_retries = max(
            0,
            int(
                openai_decompose_max_retries
                if openai_decompose_max_retries is not None
                else self._openai_max_retries
            ),
        )
        self._openai_decompose_max_input_chars = max(1000, int(openai_decompose_max_input_chars))
        self.tts_provider = tts_provider
        self.fallback_tts_provider = fallback_tts_provider
        self.tts_voice = tts_voice or kokoro_voice
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
        self._on_record_video = on_record_video
        self._on_open_code_editor = on_open_code_editor
        self._on_open_html_editor = on_open_html_editor
        self._on_start_timer = on_start_timer
        self._on_token_usage = on_token_usage
        self._on_section_advanced = on_section_advanced
        self._on_curriculum_complete = on_curriculum_complete
        self._on_turn_complete = on_turn_complete
        self._on_response_end = on_response_end
        self._on_tts_playing = on_tts_playing
        self._on_tts_done = on_tts_done
        self._on_error = on_error

        self._audio_turns: list[list[np.ndarray]] = []
        self._audio_lock = threading.Lock()
        self.last_audio: np.ndarray | None = None

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def audio_turns(self) -> list[list[np.ndarray]]:
        return self._audio_turns

    def set_tts_voice(self, voice: str) -> None:
        """Set the currently selected voice for the active provider."""
        self.tts_voice = voice
        # Keep backward compatibility with older code paths.
        self.kokoro_voice = voice

    def decompose_pdf(
        self,
        pdf_path: str,
        on_progress: Callable[[str], None] | None = None,
        student_goal: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Curriculum:
        """
        Decompose a PDF into a Curriculum using a two-phase parallel pipeline.

        Phase 1 (cheap, serial): find natural segment boundaries from the
        embedded TOC or a fast Haiku call on extracted structural text.

        Phase 2 (rich, parallel): each segment is analysed as a full PDF slice
        with the configured LLM and optional web-search augmentation.  All
        segments run concurrently via a thread pool so wall-clock time is
        bounded by the slowest segment rather than the sum.

        Synchronous; call from a background thread.
        """
        import fitz  # pymupdf
        from concurrent.futures import ThreadPoolExecutor, as_completed

        decompose_provider = self._decompose_llm_provider
        anthropic_client = None
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        supports_thinking = "haiku" not in self._decompose_llm_model

        if decompose_provider != "openai":
            import anthropic

            anthropic_client = anthropic.Anthropic(max_retries=6)

        goal_note = (
            f"\n\nSTUDENT GOAL: \"{student_goal}\"\n"
            "Use this to inform your decomposition:\n"
            "- Break content most relevant to this goal into finer, more precise sections.\n"
            "- Be especially careful with page_start/page_end for those sections.\n"
            "- Prioritise key_concepts that directly serve this goal."
            if student_goal else ""
        )

        # ── Phase 1: find segment boundaries (single-threaded, cheap) ─────────
        if on_progress:
            on_progress("Analysing document structure…")

        if decompose_provider == "openai":
            toc_segs = _toc_segments(doc, total_pages, SEGMENT_TARGET_PAGES)
            if toc_segs:
                segments, doc_title = toc_segs, None
            else:
                segments, doc_title = [
                    (start, min(start + SEGMENT_TARGET_PAGES, total_pages))
                    for start in range(0, total_pages, SEGMENT_TARGET_PAGES)
                ], None
        else:
            segments, doc_title = _find_segments(doc, anthropic_client, total_pages)
        n_segs = len(segments)

        if on_progress and n_segs > 1:
            on_progress(f"Found {n_segs} segments — decomposing in parallel…")

        # Extract PDF bytes for each segment while the doc is open.
        # PyMuPDF operations stay single-threaded; threads only see bytes.
        segment_bytes: list[bytes] = []
        for start, end in segments:
            chunk = fitz.open()
            chunk.insert_pdf(doc, from_page=start, to_page=end - 1)
            segment_bytes.append(chunk.tobytes())
            chunk.close()

        doc.close()

        # ── Phase 2: parallel segment decomposition ───────────────────────────
        ordered_sections: list[list[dict]] = [[] for _ in range(n_segs)]
        first_llm_title: str | None = None

        def _analyse(idx: int, pdf_bytes: bytes, start: int, end: int):
            if cancel_event and cancel_event.is_set():
                return idx, (None, [])
            if on_progress:
                on_progress(
                    f"Decomposing pages {start + 1}–{end} of {total_pages}…"
                    if n_segs > 1 else "Decomposing document…"
                )
            if decompose_provider == "openai":
                return idx, self._decompose_segment_bytes_openai(
                    pdf_bytes,
                    start,
                    end,
                    total_pages,
                    goal_note,
                    cancel_event,
                )
            return idx, self._decompose_segment_bytes(
                anthropic_client,
                pdf_bytes,
                start,
                end,
                total_pages,
                goal_note,
                supports_thinking,
                on_progress,
                cancel_event,
            )

        pool = ThreadPoolExecutor(max_workers=min(n_segs, MAX_SEGMENT_WORKERS))
        try:
            futures = [
                pool.submit(_analyse, i, segment_bytes[i], segments[i][0], segments[i][1])
                for i in range(n_segs)
            ]
            for future in as_completed(futures):
                idx, (seg_title, sections) = future.result()
                ordered_sections[idx] = sections
                if idx == 0 and seg_title and first_llm_title is None:
                    first_llm_title = seg_title
        finally:
            # cancel_futures=True drops queued segments that haven't started yet;
            # already-running threads check cancel_event before their next API call.
            pool.shutdown(wait=False, cancel_futures=True)

        all_sections = [s for seg in ordered_sections for s in seg]
        all_sections.sort(key=lambda s: s.get("page_start", 0))

        return Curriculum(
            title=first_llm_title or doc_title or Path(pdf_path).stem,
            sections=all_sections,
        )

    def _decompose_segment_bytes(
        self,
        client,
        pdf_bytes: bytes,
        seg_start: int,
        seg_end: int,
        total_pages: int,
        goal_note: str,
        supports_thinking: bool,
        on_progress: Callable[[str], None] | None,
        cancel_event: threading.Event | None = None,
    ) -> tuple[str | None, list[dict]]:
        """Analyse one PDF segment (raw bytes). Returns (title|None, sections).

        This is the inner loop of the former monolithic decompose_pdf, extracted
        so it can be called in parallel from a thread pool.
        """
        pdf_data = base64.standard_b64encode(pdf_bytes).decode("utf-8")

        chunk_note = (
            f"\nNote: This is pages {seg_start + 1}–{seg_end} of {total_pages}. "
            "Extract sections only from these pages."
            if total_pages > (seg_end - seg_start) else ""
        )

        messages: list[dict] = [{
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
                {"type": "text", "text": DECOMPOSE_PROMPT + chunk_note + goal_note},
            ],
        }]

        search_calls = 0
        MAX_SEARCH_CALLS = 3

        while True:
            if cancel_event and cancel_event.is_set():
                return None, []

            with client.messages.stream(
                model=self._decompose_llm_model,
                max_tokens=32000,
                **({"thinking": {"type": "adaptive"}} if supports_thinking else {}),
                system=DECOMPOSE_SYSTEM,
                tools=[SEARCH_WEB_TOOL],
                messages=messages,
            ) as stream:
                response = stream.get_final_message()

            if self._on_token_usage:
                self._on_token_usage("decompose_pdf", self._decompose_llm_model, response.usage)

            tool_block = next(
                (b for b in response.content if getattr(b, "type", None) == "tool_use"),
                None,
            )

            if tool_block is None or tool_block.name != "search_web":
                raw = next(
                    (b.text for b in response.content if getattr(b, "type", None) == "text"),
                    None,
                )
                if raw is None:
                    raise ValueError("Decompose agent returned no text block")
                raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
                start_idx = raw.find("{")
                end_idx = raw.rfind("}")
                if start_idx >= 0 and end_idx > start_idx:
                    raw = raw[start_idx : end_idx + 1]
                data = json.loads(raw)
                return data.get("title"), data.get("sections", [])

            query = tool_block.input.get("query", "")
            messages.append({
                "role": "assistant",
                "content": [_block_to_api_dict(b) for b in response.content],
            })

            if search_calls >= MAX_SEARCH_CALLS:
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_block.id,
                        "content": "Search limit reached. Please provide the final decomposition JSON now.",
                    }],
                })
                continue

            search_calls += 1
            if on_progress:
                on_progress(f"Researching: {query[:70]}…")

            search_result = self._run_web_search(query)
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": search_result,
                }],
            })

    def _decompose_segment_bytes_openai(
        self,
        pdf_bytes: bytes,
        seg_start: int,
        seg_end: int,
        total_pages: int,
        goal_note: str,
        cancel_event: threading.Event | None = None,
    ) -> tuple[str | None, list[dict]]:
        """OpenAI fallback decomposition path for one PDF segment."""
        import fitz

        if cancel_event and cancel_event.is_set():
            return None, []

        seg_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            text_blocks: list[str] = []
            for i, page in enumerate(seg_doc):
                text = page.get_text().strip()
                if not text:
                    continue
                absolute_page = seg_start + i + 1
                text_blocks.append(f"[Page {absolute_page}]\n{text}")
        finally:
            seg_doc.close()

        combined_text = "\n\n".join(text_blocks).strip()
        if not combined_text:
            return None, []
        combined_text = combined_text[: self._openai_decompose_max_input_chars]

        chunk_note = (
            f"\nNote: This is pages {seg_start + 1}–{seg_end} of {total_pages}. "
            "Extract sections only from these pages."
            if total_pages > (seg_end - seg_start)
            else ""
        )
        prompt = (
            DECOMPOSE_PROMPT
            + chunk_note
            + goal_note
            + "\n\nSOURCE TEXT EXTRACT:\n"
            + combined_text
            + "\n\nReturn JSON only."
        )
        content_blocks, content_text, usage = self._openai_chat_turn(
            model=self._decompose_llm_model,
            system=DECOMPOSE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            timeout_seconds=self._openai_decompose_timeout_seconds,
            max_retries=self._openai_decompose_max_retries,
        )
        if self._on_token_usage:
            self._on_token_usage("decompose_pdf", self._decompose_llm_model, usage)

        raw = content_text.strip()
        if not raw and content_blocks:
            raw = "\n".join(
                str(b.get("text", "")).strip()
                for b in content_blocks
                if isinstance(b, dict) and b.get("type") == "text"
            ).strip()
        if not raw:
            raise ValueError("OpenAI decompose agent returned no text block")
        raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
        start_idx = raw.find("{")
        end_idx = raw.rfind("}")
        if start_idx >= 0 and end_idx > start_idx:
            raw = raw[start_idx : end_idx + 1]
        data = json.loads(raw)
        return data.get("title"), data.get("sections", [])

    def _run_web_search(self, query: str) -> str:
        """
        Guardrailed web search subagent.

        Uses Anthropic's built-in web_search tool (beta) restricted to
        trustworthy sources.  Falls back gracefully if the beta is unavailable.
        """
        import anthropic

        client = anthropic.Anthropic(max_retries=3)
        try:
            response = client.beta.messages.create(
                model=self._decompose_llm_model,
                max_tokens=1024,
                system=SEARCH_GUARDRAIL_SYSTEM,
                betas=["web-search-2025-03-05"],
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
                messages=[{
                    "role": "user",
                    "content": (
                        f"Find factual information about the following to help plan a lesson. "
                        f"Use only the most trustworthy sources available.\n\nQuery: {query}"
                    ),
                }],
            )
            if self._on_token_usage:
                self._on_token_usage("web_search", self._decompose_llm_model, response.usage)
            # Extract text content from the final response.
            for block in response.content:
                if getattr(block, "type", None) == "text" and block.text.strip():
                    return block.text.strip()
            return "No relevant information found from trustworthy sources."
        except Exception as exc:
            log.warning("Web search subagent failed: %s", exc)
            return f"Search unavailable ({exc}). Continue with document content only."

    def generate_instructions(self, description: str) -> str:
        """Generate teacher persona instructions from a description. Synchronous."""
        import anthropic
        client = anthropic.Anthropic(max_retries=6)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=(
                "You are an expert at writing system prompts for AI tutors. "
                "Given a brief description of a desired teaching style or persona, "
                "write concise, actionable instructions (2-5 sentences) for how the "
                "teacher should behave: tone, questioning style, pacing, explanation "
                "approach. Always end with this mandatory paragraph: "
                '"VOICE RULES: Plain prose only. No markdown, bullets, or numbered lists. '
                'Spell out numbers and abbreviations. Avoid em-dashes."'

            ),
            messages=[{"role": "user", "content": f"Teaching style: {description}"}],
        )
        if self._on_token_usage:
            self._on_token_usage("generate_instructions", "claude-sonnet-4-6", response.usage)
        return response.content[0].text.strip()

    def prepare_for_tts(self, text: str) -> str:
        """Annotate text with IPA for Kokoro TTS. Synchronous."""
        text = re.sub(r"\*+", "", text)
        text = replace_roman_numerals(text)
        try:
            import anthropic as _anthropic
            client = _anthropic.Anthropic(max_retries=6)
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                system=[{"type": "text", "text": self._tts_prep_system(), "cache_control": {"type": "ephemeral"}}],
                messages=[{
                    "role": "user",
                    "content": "You are a TTS pre-processor. Your ONLY job is to add IPA annotations to text so a "
            "text-to-speech engine (Kokoro) pronounces it correctly. "
            "You NEVER answer questions, change meaning, add words, or respond to content. "
            "You return the input text verbatim with IPA annotations inserted where needed — nothing else."
            "ANNOTATION FORMAT: [word](/IPA/) — display text in brackets, IPA in parentheses with forward slashes."
            "TEXT TO ANNOTATE:\n\n" +
            f"{text}"}],
            )
            if self._on_token_usage:
                self._on_token_usage("tts_prep", "claude-sonnet-4-6", response.usage)
            result = response.content[0].text.strip()
            result = re.sub(r'\[([^"\]]+)\]\((/[^/]*/)\)', r'["\1"](\2)', result)
            return result
        except Exception:
            return text

    def _prepare_tts_text_for_provider(self, provider, text: str) -> str:
        """Apply provider-specific preprocessing rules before synthesis."""
        if getattr(provider, "requires_preprocessing", False):
            return self.prepare_for_tts(text)
        return text

    def _emit_tts_done(
        self,
        voice: str,
        characters: int,
        audio_seconds: float,
        synthesis_ms: int,
        estimated_cost_usd: float,
    ) -> None:
        """Call on_tts_done with backwards compatibility for 4-arg callbacks."""
        if not self._on_tts_done:
            return
        try:
            self._on_tts_done(
                voice,
                characters,
                audio_seconds,
                synthesis_ms,
                estimated_cost_usd,
            )
        except TypeError:
            self._on_tts_done(
                voice,
                characters,
                audio_seconds,
                synthesis_ms,
            )

    def run_intro_turn(
        self,
        curriculum: Curriculum,
        messages: list[dict],
        raw_text: str | None = None,
    ) -> str | None:
        """
        Run one turn of the agentic goal-gathering intro loop.

        Returns the captured lesson goal string if the agent called
        capture_lesson_goal, otherwise None (agent asked a follow-up; caller
        should collect the student's response and call again).

        Modifies messages in place.  Synchronous; call from a background thread.
        """
        if not messages:
            messages.append({"role": "user", "content": "Please begin."})

        tool = self._do_single_llm_turn(
            curriculum,
            messages,
            agent_instructions=None,
            _system=make_intro_prompt(curriculum.title, curriculum.sections, raw_text),
            _tools=[CAPTURE_GOAL_TOOL],
            _call_type="intro_turn",
        )

        if tool is not None and tool.name == "capture_lesson_goal":
            goal = tool.input.get("goal", "").strip()
            depth = tool.input.get("depth", "")
            # Keep conversation valid: add a tool_result placeholder.
            messages.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool.id, "content": "Goal captured."}],
            })
            if self._on_turn_complete:
                self._on_turn_complete(self.last_audio)
            return f"{goal} (level: {depth})" if depth else (goal or "Learn the material.")

        # Agent responded with text only (or unrecognised tool) — asked a follow-up.
        if tool is not None:
            # Unrecognised tool: add placeholder and keep looping next call.
            messages.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool.id, "content": "OK"}],
            })
        if self._on_turn_complete:
            self._on_turn_complete(self.last_audio)
        return None

    def run_intro(self, curriculum: Curriculum, messages: list[dict], raw_text: str | None = None) -> str | None:
        """Backward-compat wrapper — runs the first intro turn."""
        return self.run_intro_turn(curriculum, messages, raw_text)

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
        if not curriculum.sections:
            raise ValueError(
                "No lesson sections are available for teaching. Decompose the document first."
            )

        if not messages:
            messages.append({"role": "user", "content": "Please begin teaching."})

        # Remove any trailing assistant message that has a tool_use block without a
        # following tool_result — this can happen if the session disconnected mid-turn
        # and _save_state persisted the partial conversation.
        _strip_dangling_tool_use(messages)

        while True:
            tool = self._do_single_llm_turn(curriculum, messages, agent_instructions, lesson_goal=lesson_goal)

            if tool is None:
                # No tool call — turn is done; enable recording.
                if self._on_turn_complete:
                    self._on_turn_complete(self.last_audio)
                return

            # Interactive tools (sketchpad, photo, code editor…) block the thread
            # on done_event.wait() until the student responds.  If the session is
            # interrupted while waiting, we must NOT leave a dangling tool_use in
            # the message history.  Strategy: pop the just-appended assistant
            # message, wait for the result, then re-append assistant + tool_result
            # together atomically so messages are never saved in a partial state.
            _INTERACTIVE_TOOLS = frozenset({
                "open_sketchpad", "take_photo", "record_video",
                "open_code_editor", "open_html_editor", "start_timer",
            })
            if tool.name in _INTERACTIVE_TOOLS:
                pending_assistant_msg = messages.pop()
            else:
                # Non-interactive tools resolve immediately — safe to commit now.
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool.id, "content": "OK"}],
                })
                pending_assistant_msg = None  # not used below

            if tool.name == "advance_to_next_section":
                if curriculum.is_last:
                    if self._on_curriculum_complete:
                        self._on_curriculum_complete()
                    return
                curriculum.idx += 1
                if self._on_section_advanced:
                    self._on_section_advanced(curriculum)
                episode_summary = self._condense_episode(messages, curriculum)
                messages.clear()
                if episode_summary:
                    messages.append({
                        "role": "user",
                        "content": (
                            "[Student profile from the previous section — use this to personalise "
                            "your teaching style and pacing for this section]:\n"
                            f"{episode_summary}"
                        ),
                    })
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
                messages.append(pending_assistant_msg)
                messages.append({
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
                })
                # Loop → agent evaluates the sketch.

            elif tool.name == "take_photo":
                prompt = tool.input.get("prompt", "Please take a photo.")
                result_holder_p: list[str | None] = [None]
                done_event_p = threading.Event()
                if self._on_take_photo:
                    self._on_take_photo(prompt, result_holder_p, done_event_p)
                done_event_p.wait()
                messages.append(pending_assistant_msg)
                messages.append({
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
                })
                # Loop → agent sees the photo.

            elif tool.name == "record_video":
                prompt = tool.input.get("prompt", "Please record a short video.")
                result_holder_v: list[list[str] | None] = [None]
                done_event_v = threading.Event()
                if self._on_record_video:
                    self._on_record_video(prompt, result_holder_v, done_event_v)
                done_event_v.wait()
                frames: list[str] = result_holder_v[0] or []
                frame_blocks = [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": frame,
                        },
                    }
                    for frame in frames
                ]
                frame_blocks.append({
                    "type": "text",
                    "text": (
                        f"The student recorded a video. These are {len(frames)} evenly-spaced "
                        "frames sampled from the recording. Please observe the sequence and respond."
                    ),
                })
                messages.append(pending_assistant_msg)
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool.id, "content": frame_blocks}],
                })
                # Loop → agent evaluates the video frames.

            elif tool.name == "open_code_editor":
                prompt = tool.input.get("prompt", "Complete the coding challenge.")
                language = tool.input.get("language", "python")
                starter_code: str | None = tool.input.get("starter_code")
                result_holder_ce: list[dict | None] = [None]
                done_event_ce = threading.Event()
                if self._on_open_code_editor:
                    self._on_open_code_editor(prompt, language, starter_code, result_holder_ce, done_event_ce)
                done_event_ce.wait()
                r = result_holder_ce[0] or {}
                code = r.get("code", "")
                stdout = r.get("stdout", "")
                stderr = r.get("stderr", "")
                exit_code = r.get("exit_code", -1)
                content = (
                    f"The student submitted the following {language} code:\n\n"
                    f"```{language}\n{code}\n```\n\n"
                    f"Execution output:\n"
                    f"stdout: {stdout or '(none)'}\n"
                    f"stderr: {stderr or '(none)'}\n"
                    f"exit code: {exit_code}"
                )
                messages.append(pending_assistant_msg)
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool.id, "content": content}],
                })
                # Loop → agent reviews and gives feedback.

            elif tool.name == "open_html_editor":
                prompt = tool.input.get("prompt", "Complete the HTML/CSS challenge.")
                starter_html: str | None = tool.input.get("starter_html")
                starter_css: str | None = tool.input.get("starter_css")
                result_holder_he: list[dict | None] = [None]
                done_event_he = threading.Event()
                if self._on_open_html_editor:
                    self._on_open_html_editor(prompt, starter_html, starter_css, result_holder_he, done_event_he)
                done_event_he.wait()
                r = result_holder_he[0] or {}
                html = r.get("html", "")
                css = r.get("css", "")
                content = (
                    f"The student submitted the following HTML/CSS:\n\n"
                    f"HTML:\n```html\n{html}\n```\n\n"
                    f"CSS:\n```css\n{css}\n```"
                )
                messages.append(pending_assistant_msg)
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool.id, "content": content}],
                })
                # Loop → agent reviews and gives feedback.

            elif tool.name == "start_timer":
                prompt = tool.input.get("prompt", "Complete the exercise.")
                duration_seconds = int(tool.input.get("duration_seconds", 60))
                result_holder_ti: list[dict | None] = [None]
                done_event_ti = threading.Event()
                if self._on_start_timer:
                    self._on_start_timer(prompt, duration_seconds, result_holder_ti, done_event_ti)
                done_event_ti.wait()
                r = result_holder_ti[0] or {}
                timed_out: bool = r.get("timed_out", True)
                answer: str = (r.get("answer") or "").strip()
                elapsed: int | None = r.get("elapsed_seconds")
                if timed_out:
                    content = (
                        f"Time expired. The student's answer: {answer}"
                        if answer else "Time expired. The student did not submit an answer."
                    )
                else:
                    time_str = f"{elapsed}s" if elapsed is not None else "early"
                    content = (
                        f"The student submitted early ({time_str}). Answer: {answer}"
                        if answer else f"The student submitted early ({time_str}) without writing an answer."
                    )
                messages.append(pending_assistant_msg)
                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool.id, "content": content}],
                })
                # Loop → agent gives feedback.

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
            f"{IPA_REFERENCE}\n\n"
            "Example — input:  She read the kanji 猫 and said c'est la vie.\n"
            "Example — output: She [read](/rɛd/) the kanji [猫](/nɛ̞ko̞/) and said [c'est la vie](/sɛ la vi/).\n"
        )

    def _condense_episode(self, messages: list[dict], curriculum: Curriculum) -> str:
        """
        Condense the completed section's conversation into a brief student profile.
        curriculum.idx already points to the new section; completed = idx - 1.
        Returns plain text to inject as the opening message for the new section.
        """
        import anthropic

        completed_section = curriculum.sections[curriculum.idx - 1]

        transcript_parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, str):
                if content.strip():
                    transcript_parts.append(f"{role.upper()}: {content.strip()}")
            elif isinstance(content, list):
                texts = [
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                combined = " ".join(t for t in texts if t).strip()
                if combined:
                    transcript_parts.append(f"{role.upper()}: {combined}")

        if not transcript_parts:
            return ""

        client = anthropic.Anthropic(max_retries=6)
        response = client.messages.create(
            model=self._llm_model,
            max_tokens=400,
            system=(
                "You are a teaching assistant. Analyse this tutoring session transcript and write "
                "a concise student profile (3-5 sentences) for the teacher of the next section. "
                "Focus entirely on the student — not on what was taught. Cover: what they grasped "
                "quickly, where they struggled or needed re-explanation, their preferred pace, "
                "the tone and question styles that engaged them, and any patterns in their answers. "
                "Be specific and actionable."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Completed section: \"{completed_section.get('title', 'Unknown')}\"\n\n"
                    f"Transcript:\n{chr(10).join(transcript_parts)}\n\n"
                    "Write the student profile for the incoming teacher."
                ),
            }],
        )
        if self._on_token_usage:
            self._on_token_usage("episode_condensation", self._llm_model, response.usage)
        return response.content[0].text.strip()

    def _openai_chat_turn(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict],
        tools: list | None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> tuple[list[dict], str, object]:
        """
        Execute one OpenAI chat-completions turn and return:
          - assistant content blocks in internal format
          - assistant text
          - usage object compatible with record_api expectations
        """
        import httpx

        if not self._openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured for OpenAI teaching turns.")

        payload: dict = {
            "model": model,
            "max_tokens": 2048,
            "messages": [{"role": "system", "content": system}] + _messages_to_openai(messages),
        }
        if tools:
            payload["tools"] = [_tool_schema_to_openai(t) for t in tools]

        headers = {"Authorization": f"Bearer {self._openai_api_key}"}
        url = "https://api.openai.com/v1/chat/completions"
        last_err: Exception | None = None
        timeout_value = max(1.0, float(timeout_seconds if timeout_seconds is not None else self._openai_timeout_seconds))
        retry_count = max(0, int(max_retries if max_retries is not None else self._openai_max_retries))

        for attempt in range(retry_count + 1):
            try:
                with httpx.Client(timeout=timeout_value) as client:
                    resp = client.post(url, headers=headers, json=payload)
                if resp.status_code >= 400:
                    raise RuntimeError(_format_openai_chat_error(resp))

                data = resp.json()
                choice = (data.get("choices") or [{}])[0]
                message = choice.get("message") or {}

                raw_content = message.get("content") or ""
                if isinstance(raw_content, list):
                    text_parts = []
                    for part in raw_content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            txt = (part.get("text") or "").strip()
                            if txt:
                                text_parts.append(txt)
                    content_text = "\n".join(text_parts).strip()
                else:
                    content_text = str(raw_content).strip()

                content_blocks: list[dict] = []
                if content_text:
                    content_blocks.append({"type": "text", "text": content_text})

                for tc in message.get("tool_calls") or []:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function") or {}
                    args_raw = fn.get("arguments") or "{}"
                    try:
                        parsed_args = (
                            json.loads(args_raw)
                            if isinstance(args_raw, str)
                            else (args_raw if isinstance(args_raw, dict) else {})
                        )
                    except Exception:
                        parsed_args = {}
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id") or "",
                        "name": fn.get("name") or "",
                        "input": parsed_args,
                    })

                usage = data.get("usage") or {}
                usage_obj = SimpleNamespace(
                    input_tokens=int(usage.get("prompt_tokens") or 0),
                    output_tokens=int(usage.get("completion_tokens") or 0),
                    cache_read_input_tokens=int(
                        (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
                    ),
                    cache_creation_input_tokens=0,
                )

                return content_blocks, content_text, usage_obj
            except Exception as exc:
                last_err = exc
                if attempt >= retry_count:
                    break
                time.sleep(min(0.2 * (2**attempt), 1.0))

        raise RuntimeError(f"OpenAI teaching turn failed: {last_err}") from last_err

    def _do_single_llm_turn(
        self,
        curriculum: Curriculum,
        messages: list[dict],
        agent_instructions: str | None,
        lesson_goal: str | None = None,
        _system: str | None = None,
        _tools: list | None = None,
        _call_type: str | None = None,
    ):
        """
        Run one LLM streaming call, pipeline TTS synthesis and playback in parallel.
        Appends the assistant message to messages.
        Returns the first tool_use block, or None if no tool was called.
        """
        llm_provider = self._teach_llm_provider
        llm_model = self._teach_llm_model or self._llm_model
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
            active_provider = self.tts_provider
            fallback_provider = self.fallback_tts_provider
            fallback_engaged = False

            while True:
                text = tts_queue.get()
                if text is None:
                    audio_queue.put(_STOP)
                    return

                # Provider path (hybrid mode): OpenAI primary, Kokoro fallback.
                if active_provider is not None:
                    try:
                        speakable_text = self._prepare_tts_text_for_provider(active_provider, text)
                        result = active_provider.synthesize(speakable_text, self.tts_voice)
                    except Exception as exc:
                        if (
                            not fallback_engaged
                            and fallback_provider is not None
                            and active_provider is not fallback_provider
                        ):
                            fallback_engaged = True
                            active_provider = fallback_provider
                            if self._on_error:
                                self._on_error(
                                    f"TTS provider failed ({exc}). Switching to Kokoro fallback for this turn."
                                )
                            try:
                                speakable_text = self._prepare_tts_text_for_provider(active_provider, text)
                                result = active_provider.synthesize(speakable_text, self.tts_voice)
                            except Exception as fallback_exc:
                                if self._on_error:
                                    self._on_error(str(fallback_exc))
                                # Keep turn alive even if speech synthesis fails.
                                audio_queue.put(np.zeros(0, dtype=np.float32))
                                continue
                        else:
                            if self._on_error:
                                self._on_error(str(exc))
                            # Keep turn alive even if speech synthesis fails.
                            audio_queue.put(np.zeros(0, dtype=np.float32))
                            continue

                    audio_queue.put(result.audio)
                    self.kokoro_voice = getattr(result, "voice", self.kokoro_voice)
                    self._emit_tts_done(
                        getattr(result, "voice", self.tts_voice),
                        getattr(result, "characters", len(text)),
                        len(result.audio) / max(1, getattr(result, "sample_rate", KOKORO_SAMPLE_RATE)),
                        getattr(result, "synthesis_ms", 0),
                        float(getattr(result, "estimated_cost_usd", 0.0)),
                    )
                    continue

                # Legacy path: local Kokoro only.
                try:
                    spoken = self.prepare_for_tts(text)
                    t0 = time.monotonic()
                    chunks = [
                        np.clip(audio.numpy(), -1.0, 1.0)
                        for _, _, audio in self.kokoro_pipeline(spoken, voice=self.kokoro_voice)
                    ]
                    synthesis_ms = int((time.monotonic() - t0) * 1000)
                    combined = (
                        np.concatenate(chunks).astype(np.float32) if chunks
                        else np.zeros(0, dtype=np.float32)
                    )
                    audio_queue.put(combined)
                    self._emit_tts_done(
                        self.kokoro_voice,
                        len(spoken),
                        len(combined) / KOKORO_SAMPLE_RATE,
                        synthesis_ms,
                        0.0,
                    )
                except Exception as e:
                    if self._on_error:
                        self._on_error(str(e))
                    # Keep turn alive if TTS fails.
                    audio_queue.put(np.zeros(0, dtype=np.float32))
                    continue

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
                    if self._on_tts_playing and audio_data.size > 0:
                        self._on_tts_playing(True)
                    if self._on_audio_chunk:
                        # Backend mode: stream chunk to caller instead of local playback.
                        self._on_audio_chunk(audio_data, turn_idx, _chunk_idx)
                    elif audio_data.size > 0:
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
            call_type = _call_type or ("intro_turn" if _tools == [] else "teach_turn")

            if llm_provider == "openai":
                log.info(
                    "_do_single_llm_turn: opening OpenAI turn (model=%s, messages=%d)",
                    llm_model,
                    len(messages),
                )
                if self._on_turn_start:
                    self._on_turn_start()
                content_blocks, content_text, usage = self._openai_chat_turn(
                    model=llm_model,
                    system=system,
                    messages=messages,
                    tools=tools,
                )
                full_text = content_text
                if full_text and self._on_text_chunk:
                    # Non-streaming OpenAI v1 path: emit as one chunk.
                    self._on_text_chunk(full_text)
                text_buf += full_text
                if "\n" in text_buf:
                    parts = text_buf.split("\n")
                    for part in parts[:-1]:
                        if part.strip():
                            _flush(part.strip())
                    text_buf = parts[-1]
                elif len(text_buf.split()) >= 400:
                    _flush(text_buf.strip())
                    text_buf = ""

                if self._on_token_usage:
                    self._on_token_usage(call_type, llm_model, usage)

                messages.append({"role": "assistant", "content": content_blocks})
                for block in content_blocks:
                    if block.get("type") == "tool_use":
                        tool_use_block = SimpleNamespace(
                            type="tool_use",
                            id=block.get("id", ""),
                            name=block.get("name", ""),
                            input=block.get("input", {}),
                        )
                        break

            else:
                import anthropic

                client = anthropic.Anthropic(max_retries=6)
                stream_kwargs: dict = dict(
                    model=llm_model,
                    max_tokens=2048,
                    system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
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

                if self._on_token_usage:
                    self._on_token_usage(call_type, llm_model, final.usage)

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
