"""Phase 2 course authoring: advisor conversation + chapter decomposition jobs."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import aiosqlite

from ...app_state import registry
from ...config import settings
from ...db import connection as db, models

log = logging.getLogger(__name__)

_PROMPT_VERSION = "course_decompose_v1"
_job_tasks: dict[str, asyncio.Task] = {}


def _json_loads(value: str | None, default):
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("content", "text", "summary", "description", "body", "value"):
            txt = _coerce_text(value.get(key))
            if txt:
                return txt
        return ""
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            txt = _coerce_text(item)
            if txt:
                parts.append(txt)
        return "\n".join(parts).strip()
    return str(value).strip()


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        m = re.search(r"-?\d+", value)
        if m:
            try:
                return int(m.group(0))
            except Exception:
                return None
    return None


def _coerce_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            text = _coerce_text(item)
            if text:
                out.append(text)
        return out
    if isinstance(value, str):
        parts = re.split(r"[\n,;]+", value)
        return [p.strip() for p in parts if p.strip()]
    return []


def _extract_sections_payload(parsed: dict[str, Any]) -> Any:
    # Preferred shape.
    sections = parsed.get("sections")
    if isinstance(sections, list):
        return sections
    items = parsed.get("items")
    if isinstance(items, list):
        return items

    # Common alternate wrappers from LLMs.
    for path in (
        ("curriculum", "sections"),
        ("decomposition", "sections"),
        ("result", "sections"),
        ("output", "sections"),
        ("lesson_plan", "sections"),
    ):
        node: Any = parsed
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if isinstance(node, list):
            return node

    # Some models return "chapters" even for section prompts.
    chapters = parsed.get("chapters")
    if isinstance(chapters, list):
        return chapters

    # Last resort: treat a single section-like object as one section.
    if any(k in parsed for k in ("content", "summary", "description", "title", "section_title", "name")):
        return [parsed]
    return []


def _normalize_sections_or_raise(raw_sections: Any) -> list[dict[str, Any]]:
    """Normalize model/cache section payload and require at least one teachable section."""
    if not isinstance(raw_sections, list):
        raise ValueError("Sections payload must be a list.")

    normalized: list[dict[str, Any]] = []
    for idx, sec in enumerate(raw_sections):
        if isinstance(sec, str):
            sec = {"title": sec.strip()}
        if not isinstance(sec, dict):
            continue

        explicit_title = (
            _coerce_text(sec.get("title"))
            or _coerce_text(sec.get("section_title"))
            or _coerce_text(sec.get("name"))
        )
        title = explicit_title or f"Section {idx + 1}"

        content = ""
        for key in (
            "content",
            "teaching_content",
            "teaching_focus",
            "summary",
            "description",
            "explanation",
            "body",
            "notes",
        ):
            content = _coerce_text(sec.get(key))
            if content:
                break

        key_concepts: list[str] = []
        for concepts_key in (
            "key_concepts",
            "keyConcepts",
            "concepts",
            "learning_objectives",
            "objectives",
            "outcomes",
        ):
            key_concepts = _coerce_string_list(sec.get(concepts_key))
            if key_concepts:
                break

        if not content:
            # Some models omit content but provide strong section signals.
            if key_concepts:
                content = "Focus topics: " + "; ".join(key_concepts[:8]) + "."
            elif explicit_title:
                content = f"Teach the core ideas of {title} using the source chapter pages."

        page_start = None
        for ps_key in ("page_start", "pageStart", "start_page", "startPage", "from_page"):
            page_start = _coerce_int(sec.get(ps_key))
            if page_start is not None:
                break

        page_end = None
        for pe_key in ("page_end", "pageEnd", "end_page", "endPage", "to_page"):
            page_end = _coerce_int(sec.get(pe_key))
            if page_end is not None:
                break

        if page_start is not None and page_end is None:
            page_end = page_start
        if page_end is not None and page_start is None:
            page_start = page_end

        if not content:
            continue

        normalized.append(
            {
                "title": title,
                "content": content,
                "key_concepts": key_concepts,
                "page_start": page_start,
                "page_end": page_end,
            }
        )

    if not normalized:
        raise ValueError("No teachable sections were produced.")
    return normalized


def _extract_json_object(raw: str) -> dict[str, Any]:
    cleaned = (raw or "").strip()
    cleaned = cleaned.replace("```json", "").replace("```", "").strip()
    start_idx = cleaned.find("{")
    end_idx = cleaned.rfind("}")
    if start_idx >= 0 and end_idx > start_idx:
        cleaned = cleaned[start_idx : end_idx + 1]
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("LLM output was not a JSON object.")
    return parsed


def _format_openai_error(response) -> str:
    body = response.text
    try:
        payload = response.json()
        msg = payload.get("error", {}).get("message")
        if msg:
            body = msg
    except Exception:
        pass
    return f"OpenAI API error {response.status_code}: {body}"


def _openai_chat_text_sync(
    *,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    timeout_seconds: float,
    max_retries: int,
) -> str:
    import httpx

    api_key = (settings.OPENAI_API_KEY or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}] + messages,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    last_err: Exception | None = None
    for attempt in range(max(0, int(max_retries)) + 1):
        try:
            with httpx.Client(timeout=max(1.0, float(timeout_seconds))) as client:
                resp = client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )
            if resp.status_code == 400 and "max_tokens" in (resp.text or "") and "max_completion_tokens" in (resp.text or ""):
                # Model requires max_completion_tokens — switch and retry immediately.
                payload = {k: v for k, v in payload.items() if k != "max_tokens"}
                payload["max_completion_tokens"] = max_tokens
                continue
            if resp.status_code >= 400:
                raise RuntimeError(_format_openai_error(resp))
            data = resp.json()
            choice = ((data.get("choices") or [{}])[0] or {})
            usage = data.get("usage") or {}
            details = usage.get("completion_tokens_details") or {}
            reasoning_tokens = int(details.get("reasoning_tokens") or 0)
            if choice.get("finish_reason") == "length" and reasoning_tokens > 0 and reasoning_tokens >= usage.get("completion_tokens", 0) - 1:
                # Reasoning model consumed all tokens for thinking — remove the cap and retry.
                log.info("Reasoning model exhausted token budget on reasoning; retrying without token cap.")
                payload = {k: v for k, v in payload.items() if k not in ("max_tokens", "max_completion_tokens")}
                continue
            message = choice.get("message") or {}
            content = message.get("content") or ""
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        txt = str(part.get("text") or "").strip()
                        if txt:
                            parts.append(txt)
                text = "\n".join(parts).strip()
            else:
                text = str(content).strip()
            if not text:
                raise RuntimeError(f"OpenAI returned empty content. finish_reason={choice.get('finish_reason')} reasoning_tokens={reasoning_tokens}")
            return text
        except Exception as exc:
            last_err = exc
            if attempt >= max(0, int(max_retries)):
                break
            time.sleep(min(0.2 * (2**attempt), 1.0))

    raise RuntimeError(f"OpenAI chat failed: {last_err}") from last_err


def _openai_ocr_page_texts_sync(
    *,
    doc,
    page_start: int,
    page_end: int,
    model: str,
) -> list[str]:
    """OCR fallback for scanned PDF pages using OpenAI vision input."""
    import fitz

    ocr_texts: list[str] = []
    for idx in range(page_start - 1, page_end):
        page = doc[idx]
        # Slight upscaling improves OCR accuracy on scanned textbook pages.
        pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        image_bytes = pix.tobytes("png")
        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        user_content = [
            {
                "type": "text",
                "text": (
                    "Transcribe all readable text from this textbook page. "
                    "Preserve language and reading order. "
                    "Return plain text only, with no markdown. "
                    "If the page has no readable text, return an empty string."
                ),
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_b64}"},
            },
        ]
        text = _openai_chat_text_sync(
            model=model,
            system="You are a precise OCR transcriber.",
            messages=[{"role": "user", "content": user_content}],
            max_tokens=2000,
            timeout_seconds=settings.OPENAI_DECOMPOSE_TIMEOUT_S,
            max_retries=settings.OPENAI_DECOMPOSE_MAX_RETRIES,
        ).strip()
        if not text:
            continue
        if text.lower() in {
            "no readable text",
            "no text",
            "none",
            "(empty)",
            "empty",
            "n/a",
        }:
            continue
        ocr_texts.append(f"[Page {idx + 1}]\n{text}")
    return ocr_texts


def _advisor_opening(course_title: str, chapters: list[dict[str, Any]]) -> str:
    chapter_titles = [str(c.get("title") or "").strip() for c in chapters[:5] if c.get("title")]
    covered = ", ".join(chapter_titles) if chapter_titles else "the full textbook"
    return (
        f'Great, let’s plan "{course_title}". '
        f"I can see chapters like {covered}. "
        "What are the most important learning outcomes you want students to achieve?"
    )


def _advisor_system_prompt(course_title: str, chapters: list[dict[str, Any]]) -> str:
    chapter_lines = "\n".join(
        f"- {c.get('title', 'Untitled')} (pages {c.get('page_start')}–{c.get('page_end')})"
        for c in chapters
    )
    return (
        "You are a curriculum advisor helping an instructor define decomposition objectives.\n"
        f'COURSE: "{course_title}"\n'
        f"CHAPTER DRAFTS:\n{chapter_lines}\n\n"
        "Be concise and practical. Ask clarifying questions when needed. "
        "Do not use markdown."
    )


def _advisor_reply_sync(
    *,
    course_title: str,
    chapters: list[dict[str, Any]],
    transcript: list[dict[str, str]],
) -> str:
    provider = settings.effective_authoring_llm_provider()
    model = settings.effective_authoring_llm_model()
    messages = [
        {"role": m["role"], "content": m["content"]}
        for m in transcript
        if m.get("role") in {"user", "assistant"} and str(m.get("content", "")).strip()
    ]
    try:
        if provider == "openai":
            return _openai_chat_text_sync(
                model=model,
                system=_advisor_system_prompt(course_title, chapters),
                messages=messages,
                max_tokens=500,
                timeout_seconds=settings.OPENAI_LLM_TIMEOUT_S,
                max_retries=settings.OPENAI_LLM_MAX_RETRIES,
            )
        import anthropic

        client = anthropic.Anthropic(max_retries=3)
        resp = client.messages.create(
            model=model,
            max_tokens=500,
            system=_advisor_system_prompt(course_title, chapters),
            messages=messages,
        )
        for block in getattr(resp, "content", []):
            if getattr(block, "type", None) == "text" and getattr(block, "text", "").strip():
                return str(block.text).strip()
    except Exception as exc:
        log.warning("advisor reply fallback: %s", exc)
    return (
        "Thanks, that helps. Tell me the top two concepts where you want especially rigorous "
        "assessment and any prerequisites I should emphasize."
    )


_TOC_LLM_MAX_INPUT_CHARS = 6000
_TOC_LLM_MIN_INPUT_CHARS = 300


def _extract_toc_section(text: str, max_chars: int = _TOC_LLM_MAX_INPUT_CHARS) -> str:
    """Return up to max_chars of document text most likely to contain the TOC.

    Scans for a line matching common TOC headings; if found, returns text
    starting from that line.  Falls back to the document head.
    """
    import re

    toc_pattern = re.compile(
        r"(?im)^[\s\-–—]*(?:table\s+of\s+contents?|contents?|outline)\s*$"
    )
    m = toc_pattern.search(text)
    if m:
        start = m.start()
        return text[start : start + max_chars]
    return text[:max_chars]


def _build_objectives_prompt(
    course_title: str,
    chapters: list[dict[str, Any]],
    transcript: list[dict[str, str]],
) -> str:
    """Deterministic objectives prompt built from transcript and chapter map."""
    chapter_lines = "\n".join(
        f"- {c.get('title', 'Untitled')} (pages {c.get('page_start')}–{c.get('page_end')})"
        for c in chapters
    )
    learner_goals = [
        m.get("content", "").strip()
        for m in transcript
        if m.get("role") == "user" and str(m.get("content", "")).strip()
    ]
    goals_text = (
        "\n".join(f"- {g}" for g in learner_goals[-6:]) or "- General textbook mastery"
    )
    return (
        f'DECOMPOSITION OBJECTIVES FOR "{course_title}"\n\n'
        "Prioritize these instructor goals:\n"
        f"{goals_text}\n\n"
        "Constraints:\n"
        "- Keep sections self-contained and teachable.\n"
        "- Preserve prerequisite order across chapters.\n"
        "- Emphasize concepts repeatedly referenced in the source text.\n"
        "- Include concrete, testable key concepts per section.\n\n"
        "Chapter map:\n"
        f"{chapter_lines}\n"
    )


def _objectives_and_chapters_sync(
    *,
    course_title: str,
    chapters: list[dict[str, Any]],
    transcript: list[dict[str, str]],
    extracted_text: str,
    total_pages: int,
) -> tuple[str, list[dict[str, Any]] | None]:
    """Return (objectives_prompt, suggested_chapters | None).

    Objectives prompt is always built deterministically from the transcript.
    A separate LLM call attempts to infer chapter structure from the TOC
    section of the document — skipped if the extract is too short to be useful.
    Each suggested chapter has title, page_start, page_end, included.
    """
    objectives_prompt = _build_objectives_prompt(course_title, chapters, transcript)

    toc_text = _extract_toc_section(extracted_text)
    suggested_chapters = extract_toc_chapters_sync(
        toc_text, total_pages=total_pages, course_title=course_title
    )
    return objectives_prompt, suggested_chapters


def extract_toc_chapters_sync(
    extracted_text: str,
    *,
    total_pages: int,
    course_title: str = "",
) -> list[dict[str, Any]] | None:
    """LLM-based TOC extraction. Returns a chapter list or None if extraction fails/unclear.

    Safe to call from sync context (e.g. asyncio.to_thread).
    """
    toc_text = _extract_toc_section(extracted_text)
    if len(toc_text) < _TOC_LLM_MIN_INPUT_CHARS:
        log.info("extract_toc_chapters_sync: TOC extract too short (%d chars), skipping LLM", len(toc_text))
        return None

    provider = settings.effective_authoring_llm_provider()
    model = settings.effective_authoring_llm_model()
    log.info(
        "extract_toc_chapters_sync: provider=%s model=%s toc_chars=%d total_pages=%d",
        provider, model, len(toc_text), total_pages,
    )

    system = (
        "You are a curriculum architect. Extract chapter structure from the document TOC "
        "section and return a JSON array of objects. Each object must have exactly these keys:\n"
        '  "title": string — chapter or part title\n'
        '  "page_start": integer — first page (1-indexed)\n'
        f"Page numbers must be between 1 and {total_pages}. "
        "Return only top-level chapters or parts, not sub-sections. "
        "Return a JSON array only — no markdown fences, no commentary. "
        "If the text contains no clear chapter structure, return an empty array []."
    )
    user_msg = (
        f'COURSE TITLE: "{course_title}"\n'
        f"TOTAL PAGES: {total_pages}\n\n"
        "DOCUMENT TOC SECTION:\n"
        f"{toc_text}\n\n"
        "Return the JSON array now."
    )

    raw: str | None = None
    try:
        if provider == "openai":
            raw = _openai_chat_text_sync(
                model=model,
                system=system,
                messages=[{"role": "user", "content": user_msg}],
                max_tokens=1500,
                timeout_seconds=settings.OPENAI_LLM_TIMEOUT_S,
                max_retries=settings.OPENAI_LLM_MAX_RETRIES,
            )
        else:
            import anthropic

            client = anthropic.Anthropic(max_retries=3)
            resp = client.messages.create(
                model=model,
                max_tokens=1500,
                system=system,
                messages=[{"role": "user", "content": user_msg}],
            )
            for block in getattr(resp, "content", []):
                if getattr(block, "type", None) == "text" and getattr(block, "text", "").strip():
                    raw = str(block.text).strip()
                    break
    except Exception as exc:
        log.warning("extract_toc_chapters_sync: LLM call failed: %s", exc)
        return None

    if not raw:
        return None

    try:
        stripped = raw.strip()
        if stripped.startswith("{"):
            parsed_obj = _extract_json_object(stripped)
            raw_chapters = parsed_obj.get("chapters") or parsed_obj.get("sections") or []
        else:
            raw_chapters = _json_loads(stripped, [])
        if not isinstance(raw_chapters, list):
            return None

        # Collect unique page_start values — ignore LLM-supplied page_end to avoid
        # overlap issues caused by hierarchical TOC structures. Recompute deterministically.
        candidates: list[tuple[int, str]] = []
        seen_starts: set[int] = set()
        for item in raw_chapters:
            title = _coerce_text(item.get("title", ""))
            page_start = _coerce_int(item.get("page_start"))
            if title and page_start is not None and 1 <= page_start <= total_pages:
                if page_start not in seen_starts:
                    seen_starts.add(page_start)
                    candidates.append((page_start, title))
        candidates.sort(key=lambda x: x[0])

        recomputed: list[dict[str, Any]] = []
        for i, (start, title) in enumerate(candidates):
            end = candidates[i + 1][0] - 1 if i + 1 < len(candidates) else total_pages
            if end >= start:
                recomputed.append({"title": title, "page_start": start, "page_end": end, "included": True})

        if len(recomputed) >= 2:
            log.info("extract_toc_chapters_sync: extracted %d chapters", len(recomputed))
            return recomputed

        log.info("extract_toc_chapters_sync: LLM returned <2 valid chapters, ignoring")
        return None
    except Exception as exc:
        log.warning("extract_toc_chapters_sync: parse failed: %s  raw=%r", exc, raw[:300])
        return None


async def ensure_advisor_session(
    conn: aiosqlite.Connection,
    *,
    course_id: str,
    user_id: str,
    reset: bool = False,
) -> dict[str, Any]:
    async with conn.execute(
        """SELECT transcript_json, objectives_prompt, status
           FROM course_advisor_sessions
           WHERE course_id = ?""",
        (course_id,),
    ) as cur:
        row = await cur.fetchone()

    transcript: list[dict[str, str]]
    objectives_prompt: str | None
    status: str
    if reset or row is None:
        transcript = []
        objectives_prompt = None
        status = "draft"
    else:
        transcript = _json_loads(row[0], [])
        objectives_prompt = row[1]
        status = str(row[2] or "draft")

    if not transcript:
        chapters = await _load_chapters(conn, course_id)
        transcript = [{"role": "assistant", "content": _advisor_opening(await _course_title(conn, course_id), chapters)}]

    await conn.execute(
        """INSERT INTO course_advisor_sessions
           (course_id, creator_id, transcript_json, objectives_prompt, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))
           ON CONFLICT(course_id) DO UPDATE SET
             creator_id = excluded.creator_id,
             transcript_json = excluded.transcript_json,
             objectives_prompt = excluded.objectives_prompt,
             status = excluded.status,
             updated_at = datetime('now')""",
        (course_id, user_id, json.dumps(transcript), objectives_prompt, status),
    )
    await conn.commit()
    return {
        "course_id": course_id,
        "status": status,
        "transcript": transcript,
        "objectives_prompt": objectives_prompt,
    }


async def advisor_user_message(
    conn: aiosqlite.Connection,
    *,
    course_id: str,
    user_id: str,
    text: str,
) -> dict[str, Any]:
    state = await ensure_advisor_session(conn, course_id=course_id, user_id=user_id)
    transcript = list(state["transcript"])
    transcript.append({"role": "user", "content": text.strip()})
    chapters = await _load_chapters(conn, course_id)
    course_title = await _course_title(conn, course_id)
    reply = await asyncio.to_thread(
        _advisor_reply_sync,
        course_title=course_title,
        chapters=chapters,
        transcript=transcript,
    )
    transcript.append({"role": "assistant", "content": reply})

    await conn.execute(
        """UPDATE course_advisor_sessions
           SET transcript_json = ?, status = 'draft', updated_at = datetime('now')
           WHERE course_id = ?""",
        (json.dumps(transcript), course_id),
    )
    await conn.commit()
    return {"course_id": course_id, "status": "draft", "transcript": transcript, "assistant": reply}


async def finalize_advisor_session(
    conn: aiosqlite.Connection,
    *,
    course_id: str,
    user_id: str,
) -> dict[str, Any]:
    state = await ensure_advisor_session(conn, course_id=course_id, user_id=user_id)
    transcript = list(state["transcript"])
    chapters = await _load_chapters(conn, course_id)
    source = await _load_source(conn, course_id)
    pdf_full = settings.STORAGE_DIR / str(source["pdf_path"])
    toc_was_empty = not _json_loads(source.get("toc_json"), [])

    def _extract_preview() -> str:
        from .pdf_tools import extract_text_plain

        return extract_text_plain(str(pdf_full))

    extracted_text = await asyncio.to_thread(_extract_preview)
    course_title = await _course_title(conn, course_id)
    objectives_prompt, suggested_chapters = await asyncio.to_thread(
        _objectives_and_chapters_sync,
        course_title=course_title,
        chapters=chapters,
        transcript=transcript,
        extracted_text=extracted_text,
        total_pages=int(source["page_count"]),
    )
    await conn.execute(
        """UPDATE course_advisor_sessions
           SET objectives_prompt = ?, status = 'finalized', updated_at = datetime('now')
           WHERE course_id = ?""",
        (objectives_prompt, course_id),
    )

    # When the PDF had no embedded bookmarks AND chapters are still generic
    # fallback placeholders ("Part N"), replace them with LLM-inferred structure.
    # If creation-time LLM extraction already ran, the chapter titles will be
    # meaningful (not "Part N"), and we should not overwrite them again here.
    import re as _re
    chapters_are_fallback = chapters and all(
        _re.match(r"^Part\s+\d+$", c.get("title", ""), _re.IGNORECASE)
        for c in chapters
    )
    updated_chapters: list[dict[str, Any]] | None = None
    if toc_was_empty and chapters_are_fallback and suggested_chapters:
        log.info(
            "Replacing %d fallback chapter drafts with %d LLM-inferred chapters for course %s",
            len(chapters),
            len(suggested_chapters),
            course_id,
        )
        await _write_chapter_drafts(conn, course_id=course_id, chapters=suggested_chapters)
        updated_chapters = await _load_chapters(conn, course_id)

    await conn.commit()
    return {
        "course_id": course_id,
        "status": "finalized",
        "transcript": transcript,
        "objectives_prompt": objectives_prompt,
        "chapters": updated_chapters,
    }


async def create_decomposition_job(
    conn: aiosqlite.Connection,
    *,
    course_id: str,
    user_id: str,
    notify_session_id: str | None,
    objectives_prompt_override: str | None = None,
    decompose_mode: str = "pdf",
) -> dict[str, Any]:
    async with conn.execute(
        """SELECT id, status FROM course_decomposition_jobs
           WHERE course_id = ?
             AND status IN ('queued', 'running')
           ORDER BY created_at DESC
           LIMIT 1""",
        (course_id,),
    ) as cur:
        active = await cur.fetchone()
    if active is not None:
        raise RuntimeError("A decomposition job is already in progress for this course.")

    async with conn.execute(
        "SELECT objectives_prompt, status FROM course_advisor_sessions WHERE course_id = ?",
        (course_id,),
    ) as cur:
        advisor = await cur.fetchone()
    override_prompt = (objectives_prompt_override or "").strip()
    if override_prompt:
        objectives_prompt = override_prompt
        await conn.execute(
            """INSERT INTO course_advisor_sessions
               (course_id, creator_id, transcript_json, objectives_prompt, status, created_at, updated_at)
               VALUES (?, ?, '[]', ?, 'finalized', datetime('now'), datetime('now'))
               ON CONFLICT(course_id) DO UPDATE SET
                 creator_id = excluded.creator_id,
                 objectives_prompt = excluded.objectives_prompt,
                 status = 'finalized',
                 updated_at = datetime('now')""",
            (course_id, user_id, objectives_prompt),
        )
    else:
        objectives_prompt = str(advisor[0] or "").strip() if advisor else ""
        advisor_status = str(advisor[1] or "draft") if advisor else "draft"
        if advisor_status != "finalized" or not objectives_prompt:
            raise RuntimeError("Finalize advisor objectives before starting decomposition.")

    chapters = await _load_chapters(conn, course_id, included_only=True)
    if not chapters:
        raise RuntimeError("No included chapters available for decomposition.")

    job_id = db.new_id()
    await conn.execute(
        """INSERT INTO course_decomposition_jobs
           (id, course_id, creator_id, status, decompose_mode, objectives_prompt, total_items, completed_items, failed_items,
            notify_session_id, created_at, updated_at)
           VALUES (?, ?, ?, 'queued', ?, ?, ?, 0, 0, ?, datetime('now'), datetime('now'))""",
        (job_id, course_id, user_id, decompose_mode, objectives_prompt, len(chapters), notify_session_id),
    )
    for idx, chapter in enumerate(chapters):
        await conn.execute(
            """INSERT INTO course_decomposition_job_items
               (id, job_id, chapter_id, idx, title, page_start, page_end, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', datetime('now'), datetime('now'))""",
            (
                db.new_id(),
                job_id,
                str(chapter["id"]),
                idx,
                str(chapter["title"]),
                int(chapter["page_start"]),
                int(chapter["page_end"]),
            ),
        )
    await conn.commit()
    return await get_job_status(conn, course_id=course_id, job_id=job_id)


def launch_decomposition_job(job_id: str) -> None:
    existing = _job_tasks.get(job_id)
    if existing and not existing.done():
        return
    task = asyncio.create_task(_run_decomposition_job(job_id))
    _job_tasks[job_id] = task

    def _cleanup(_task: asyncio.Task) -> None:
        _job_tasks.pop(job_id, None)

    task.add_done_callback(_cleanup)


async def get_job_status(
    conn: aiosqlite.Connection,
    *,
    course_id: str,
    job_id: str | None = None,
) -> dict[str, Any]:
    if job_id:
        async with conn.execute(
            """SELECT * FROM course_decomposition_jobs
               WHERE id = ? AND course_id = ?""",
            (job_id, course_id),
        ) as cur:
            row = await cur.fetchone()
    else:
        async with conn.execute(
            """SELECT * FROM course_decomposition_jobs
               WHERE course_id = ?
               ORDER BY created_at DESC
               LIMIT 1""",
            (course_id,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return {"job": None, "items": []}

    job = dict(row)
    async with conn.execute(
        """SELECT id, chapter_id, idx, title, page_start, page_end, lesson_id, cache_key, status, error
           FROM course_decomposition_job_items
           WHERE job_id = ?
           ORDER BY idx""",
        (job["id"],),
    ) as cur:
        items = [dict(r) for r in await cur.fetchall()]

    total = int(job.get("total_items") or 0)
    completed = int(job.get("completed_items") or 0)
    job["progress_pct"] = 0 if total <= 0 else int(round((completed / total) * 100))
    # Expose creator_id under the API name user_id for backwards compatibility.
    if "creator_id" in job and "user_id" not in job:
        job["user_id"] = job["creator_id"]
    return {"job": job, "items": items}


async def _run_decomposition_job(job_id: str) -> None:
    conn = await aiosqlite.connect(str(settings.DB_PATH))
    conn.row_factory = aiosqlite.Row
    try:
        async with conn.execute(
            "SELECT * FROM course_decomposition_jobs WHERE id = ?",
            (job_id,),
        ) as cur:
            job_row = await cur.fetchone()
        if job_row is None:
            return
        job = dict(job_row)
        course_id = str(job["course_id"])
        user_id = str(job["creator_id"])
        notify_session_id = str(job.get("notify_session_id") or "").strip() or None
        objectives_prompt = str(job.get("objectives_prompt") or "")
        decompose_mode = str(job.get("decompose_mode") or "pdf").strip().lower()
        decompose_provider = settings.effective_decompose_llm_provider()
        decompose_model = settings.effective_decompose_llm_model()
        cache_model_key = f"{decompose_provider}:{decompose_model}:{decompose_mode}"

        await conn.execute(
            """UPDATE course_decomposition_jobs
               SET status = 'running', started_at = datetime('now'), updated_at = datetime('now')
               WHERE id = ?""",
            (job_id,),
        )
        await conn.commit()
        await _notify(notify_session_id, {"event": "course_decompose_progress", "course_id": course_id, "job_id": job_id, "status": "running", "message": "Decomposition started."})

        source = await _load_source(conn, course_id)
        pdf_hash = str(source["pdf_hash"])
        source_pdf_rel = str(source["pdf_path"])
        page_count = int(source["page_count"])

        # For text mode, extract the full PDF text once up front.
        full_pdf_text: str | None = None
        if decompose_mode == "text":
            from .textbook_authoring import extract_full_text_from_pdf
            full_pdf_path = settings.STORAGE_DIR / Path(source_pdf_rel)
            full_pdf_text = await asyncio.to_thread(extract_full_text_from_pdf, full_pdf_path)

        async with conn.execute(
            """SELECT * FROM course_decomposition_job_items
               WHERE job_id = ?
               ORDER BY idx""",
            (job_id,),
        ) as cur:
            items = [dict(r) for r in await cur.fetchall()]

        completed = 0
        failed = 0
        for item_idx, item in enumerate(items):
            item_id = str(item["id"])
            chapter_id = str(item["chapter_id"])
            title = str(item["title"])
            page_start = int(item["page_start"])
            page_end = int(item["page_end"])
            next_chapter_title = items[item_idx + 1]["title"] if item_idx + 1 < len(items) else None
            await conn.execute(
                """UPDATE course_decomposition_job_items
                   SET status = 'running', error = NULL, updated_at = datetime('now')
                   WHERE id = ?""",
                (item_id,),
            )
            await conn.commit()

            try:
                cache_key = _build_cache_key(
                    pdf_hash=pdf_hash,
                    page_start=page_start,
                    page_end=page_end,
                    objectives_prompt=objectives_prompt,
                    model=cache_model_key,
                    prompt_version=_PROMPT_VERSION,
                )
                cache_hit, sections = await _resolve_sections(
                    conn=conn,
                    cache_key=cache_key,
                    pdf_hash=pdf_hash,
                    source_pdf_rel=source_pdf_rel,
                    page_start=page_start,
                    page_end=page_end,
                    page_count=page_count,
                    objectives_prompt=objectives_prompt,
                    decompose_provider=decompose_provider,
                    decompose_model=decompose_model,
                    cache_model_key=cache_model_key,
                    decompose_mode=decompose_mode,
                    full_pdf_text=full_pdf_text,
                    chapter_title=title,
                    next_chapter_title=next_chapter_title,
                )
                lesson_id = await _upsert_chapter_lesson(
                    conn=conn,
                    course_id=course_id,
                    user_id=user_id,
                    chapter_id=chapter_id,
                    title=title,
                    source_pdf_rel=source_pdf_rel,
                    page_start=page_start,
                    page_end=page_end,
                    sections=sections,
                )
                await conn.execute(
                    """UPDATE course_decomposition_job_items
                       SET status = ?, lesson_id = ?, cache_key = ?, error = NULL, updated_at = datetime('now')
                       WHERE id = ?""",
                    ("cached" if cache_hit else "completed", lesson_id, cache_key, item_id),
                )
                completed += 1
            except Exception as exc:
                failed += 1
                await conn.execute(
                    """UPDATE course_decomposition_job_items
                       SET status = 'failed', error = ?, updated_at = datetime('now')
                       WHERE id = ?""",
                    (str(exc), item_id),
                )
                log.exception("chapter decomposition failed for job %s item %s", job_id, item_id)

            await conn.execute(
                """UPDATE course_decomposition_jobs
                   SET completed_items = ?, failed_items = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (completed, failed, job_id),
            )
            await conn.commit()
            total = len(items)
            pct = 0 if total <= 0 else int(round((completed / total) * 100))
            await _notify(
                notify_session_id,
                {
                    "event": "course_decompose_progress",
                    "course_id": course_id,
                    "job_id": job_id,
                    "status": "running",
                    "completed_items": completed,
                    "failed_items": failed,
                    "total_items": total,
                    "progress_pct": pct,
                    "current_chapter": title,
                },
            )

        final_status = "completed" if failed == 0 else "failed"
        final_error = None if failed == 0 else "One or more chapter decomposition jobs failed."
        await conn.execute(
            """UPDATE course_decomposition_jobs
               SET status = ?, error = ?, finished_at = datetime('now'), updated_at = datetime('now')
               WHERE id = ?""",
            (final_status, final_error, job_id),
        )
        await conn.commit()
        await _notify(
            notify_session_id,
            {
                "event": "course_decompose_complete",
                "course_id": course_id,
                "job_id": job_id,
                "status": final_status,
                "completed_items": completed,
                "failed_items": failed,
                "total_items": len(items),
            },
        )
    finally:
        await conn.close()


async def _notify(session_id: str | None, event: dict[str, Any]) -> None:
    if not session_id:
        return
    try:
        await registry.send(session_id, event)
    except Exception:
        pass


async def _resolve_sections(
    *,
    conn: aiosqlite.Connection,
    cache_key: str,
    pdf_hash: str,
    source_pdf_rel: str,
    page_start: int,
    page_end: int,
    page_count: int,
    objectives_prompt: str,
    decompose_provider: str,
    decompose_model: str,
    cache_model_key: str,
    decompose_mode: str = "pdf",
    full_pdf_text: str | None = None,
    chapter_title: str = "",
    next_chapter_title: str | None = None,
) -> tuple[bool, list[dict[str, Any]]]:
    async with conn.execute(
        "SELECT sections_json FROM decomposition_cache WHERE cache_key = ?",
        (cache_key,),
    ) as cur:
        row = await cur.fetchone()
    if row is not None:
        try:
            cached_sections = _normalize_sections_or_raise(_json_loads(row[0], []))
            return True, cached_sections
        except Exception:
            # Existing cache row is empty/invalid; invalidate and recompute.
            await conn.execute("DELETE FROM decomposition_cache WHERE cache_key = ?", (cache_key,))
            await conn.commit()

    raw_sections = await asyncio.to_thread(
        _decompose_chapter_sync,
        source_pdf_rel,
        page_start,
        page_end,
        page_count,
        objectives_prompt,
        decompose_provider,
        decompose_model,
        decompose_mode,
        full_pdf_text,
        chapter_title,
        next_chapter_title,
    )
    sections = _normalize_sections_or_raise(raw_sections)
    await conn.execute(
        """INSERT INTO decomposition_cache
           (cache_key, pdf_hash, page_start, page_end, objectives_hash, model, prompt_version, sections_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
           ON CONFLICT(cache_key) DO UPDATE SET
             sections_json = excluded.sections_json,
             updated_at = datetime('now')""",
        (
            cache_key,
            pdf_hash,
            page_start,
            page_end,
            _sha256_text(objectives_prompt),
            cache_model_key,
            _PROMPT_VERSION,
            json.dumps(sections),
        ),
    )
    await conn.commit()
    return False, sections


def _build_cache_key(
    *,
    pdf_hash: str,
    page_start: int,
    page_end: int,
    objectives_prompt: str,
    model: str,
    prompt_version: str,
) -> str:
    material = "|".join(
        [
            pdf_hash,
            str(page_start),
            str(page_end),
            _sha256_text(objectives_prompt),
            model,
            prompt_version,
        ]
    )
    return _sha256_text(material)


def _extract_chapter_text_by_title(
    full_text: str,
    chapter_title: str,
    next_chapter_title: str | None,
    max_chars: int = 120000,
) -> str:
    """Find chapter_title in full_text and return text up to next_chapter_title.

    Searches case-insensitively. Falls back progressively:
    1. Exact title match
    2. First 4 words of title
    3. First 2 words of title
    Returns empty string if nothing matches.
    """
    import re

    def _search(needle: str) -> re.Match | None:
        return re.search(re.escape(needle), full_text, re.IGNORECASE)

    def _make_short(title: str, words: int) -> str:
        return " ".join(re.sub(r"\s+", " ", title.strip()).split()[:words])

    def _strip_prefix(title: str) -> str:
        """Strip leading roman numeral or numeric prefix, e.g. 'VI. BigO' → 'BigO'."""
        stripped = re.sub(r"^(?:[IVXLCDM]+\.|\d+\.)\s+", "", title.strip(), flags=re.IGNORECASE)
        return stripped.strip() or title.strip()

    def _best_match(title: str) -> re.Match | None:
        clean = re.sub(r"\s+", " ", title.strip())
        return (
            _search(clean)
            or _search(_strip_prefix(clean))
            or _search(_make_short(clean, 4))
            or _search(_make_short(_strip_prefix(clean), 4))
            or _search(_make_short(clean, 2))
            or _search(_make_short(_strip_prefix(clean), 2))
        )

    match = _best_match(chapter_title)
    if not match:
        log.warning("_extract_chapter_text_by_title: title %r not found in full text", chapter_title)
        return ""

    start = match.start()

    if next_chapter_title:
        next_match = _best_match(next_chapter_title)
        if next_match and next_match.start() > start:
            return full_text[start : next_match.start()][:max_chars]

    return full_text[start : start + max_chars]


def _decompose_chapter_text_sync(
    *,
    full_pdf_text: str,
    chapter_title: str,
    next_chapter_title: str | None,
    objectives_prompt: str,
    decompose_provider: str,
    decompose_model: str,
) -> list[dict[str, Any]]:
    """Text-based decomposition: extract chapter text by title search, send as plain text to LLM."""
    from ..agents.prompts.decompose import DECOMPOSE_PROMPT, DECOMPOSE_SYSTEM

    chapter_text = _extract_chapter_text_by_title(
        full_pdf_text, chapter_title, next_chapter_title,
        max_chars=max(1000, int(settings.OPENAI_DECOMPOSE_MAX_INPUT_CHARS)),
    )
    if not chapter_text:
        raise ValueError(f"Could not locate chapter '{chapter_title}' in extracted PDF text.")

    text_system = DECOMPOSE_SYSTEM.replace(
        "page_start and page_end must be the exact page numbers where each section "
        "appears in the PDF. Count pages carefully — these numbers are used to display the actual "
        "PDF pages to the student during teaching.",
        "page_start and page_end may be set to null — this is text-only mode and page references are not used.",
    )
    goal_note = (
        f"\n\nSTUDENT GOAL: \"{objectives_prompt}\"\n"
        "Use this to inform your decomposition:\n"
        "- Break content most relevant to this goal into finer, more precise sections.\n"
        "- Prioritise key_concepts that directly serve this goal."
        if objectives_prompt
        else ""
    )
    user_message = (
        DECOMPOSE_PROMPT
        + f"\nNote: This is the chapter '{chapter_title}'. page_start and page_end should be null."
        + goal_note
        + "\n\nSOURCE TEXT:\n"
        + chapter_text
        + "\n\nReturn JSON only."
    )

    provider = (decompose_provider or "anthropic").strip().lower()
    if provider == "openai":
        raw = _openai_chat_text_sync(
            model=decompose_model,
            system=text_system,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=4000,
            timeout_seconds=settings.OPENAI_DECOMPOSE_TIMEOUT_S,
            max_retries=settings.OPENAI_DECOMPOSE_MAX_RETRIES,
        )
    else:
        import anthropic
        client = anthropic.Anthropic(max_retries=6)
        resp = client.messages.create(
            model=decompose_model,
            max_tokens=4000,
            system=text_system,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = ""
        for block in getattr(resp, "content", []):
            if getattr(block, "type", None) == "text":
                raw = str(block.text).strip()
                break

    parsed = _extract_json_object(raw)
    sections = _extract_sections_payload(parsed)
    if not isinstance(sections, list):
        raise ValueError(f"Text decomposition response had no valid sections list. Keys: {sorted(parsed.keys())[:12]}")

    # Null out page numbers — they're meaningless in text mode
    for sec in sections:
        sec["page_start"] = None
        sec["page_end"] = None

    return sections


def _decompose_chapter_sync(
    source_pdf_rel: str,
    page_start: int,
    page_end: int,
    total_pages: int,
    objectives_prompt: str,
    decompose_provider: str,
    decompose_model: str,
    decompose_mode: str = "pdf",
    full_pdf_text: str | None = None,
    chapter_title: str = "",
    next_chapter_title: str | None = None,
) -> list[dict[str, Any]]:
    if decompose_mode == "text":
        return _decompose_chapter_text_sync(
            full_pdf_text=full_pdf_text or "",
            chapter_title=chapter_title,
            next_chapter_title=next_chapter_title,
            objectives_prompt=objectives_prompt,
            decompose_provider=decompose_provider,
            decompose_model=decompose_model,
        )

    provider = (decompose_provider or "anthropic").strip().lower()
    if provider == "openai":
        sections = _decompose_chapter_openai_sync(
            source_pdf_rel=source_pdf_rel,
            page_start=page_start,
            page_end=page_end,
            total_pages=total_pages,
            objectives_prompt=objectives_prompt,
            decompose_model=decompose_model,
        )
    else:
        import anthropic
        import fitz
        from ..agents.planner_agent import LessonPlannerAgent

        full_pdf = settings.STORAGE_DIR / Path(source_pdf_rel)
        doc = fitz.open(str(full_pdf))
        chunk = fitz.open()
        try:
            chunk.insert_pdf(doc, from_page=page_start - 1, to_page=page_end - 1)
            pdf_bytes = chunk.tobytes()
        finally:
            chunk.close()
            doc.close()

        seg_start = page_start - 1
        seg_end = page_end
        goal_note = (
            f"\n\nSTUDENT GOAL: \"{objectives_prompt}\"\n"
            "Use this to inform your decomposition:\n"
            "- Break content most relevant to this goal into finer, more precise sections.\n"
            "- Be especially careful with page_start/page_end for those sections.\n"
            "- Prioritise key_concepts that directly serve this goal."
            if objectives_prompt
            else ""
        )
        decomposer = LessonPlannerAgent(
            decompose_llm_provider="anthropic",
            openai_api_key=None,
            openai_timeout_seconds=30.0,
            openai_max_retries=1,
            openai_max_input_chars=120000,
            model=decompose_model,
        )
        client = anthropic.Anthropic(max_retries=6)
        _, sections = decomposer._decompose_segment(  # noqa: SLF001 - intentional reuse of core decomposition logic
            client,
            pdf_bytes,
            seg_start,
            seg_end,
            total_pages,
            goal_note,
            None,
            None,
        )

    # Normalize page numbers to absolute textbook pages when model returns local segment pages.
    span = page_end - page_start + 1
    for sec in sections:
        ps = sec.get("page_start")
        pe = sec.get("page_end")
        if isinstance(ps, int) and 1 <= ps <= span:
            sec["page_start"] = ps + page_start - 1
        if isinstance(pe, int) and 1 <= pe <= span:
            sec["page_end"] = pe + page_start - 1
    return sections


def _decompose_chapter_openai_sync(
    *,
    source_pdf_rel: str,
    page_start: int,
    page_end: int,
    total_pages: int,
    objectives_prompt: str,
    decompose_model: str,
) -> list[dict[str, Any]]:
    import fitz
    from ..agents.prompts.decompose import DECOMPOSE_PROMPT, DECOMPOSE_SYSTEM

    full_pdf = settings.STORAGE_DIR / Path(source_pdf_rel)
    doc = fitz.open(str(full_pdf))
    try:
        page_texts: list[str] = []
        for idx in range(page_start - 1, page_end):
            text = doc[idx].get_text().strip()
            if text:
                page_texts.append(f"[Page {idx + 1}]\n{text}")

        combined_text = "\n\n".join(page_texts).strip()
        if not combined_text and settings.OPENAI_DECOMPOSE_ENABLE_VISION_OCR:
            log.info(
                "OpenAI decomposition text extraction empty for pages %s-%s; attempting vision OCR fallback",
                page_start,
                page_end,
            )
            ocr_texts = _openai_ocr_page_texts_sync(
                doc=doc,
                page_start=page_start,
                page_end=page_end,
                model=decompose_model,
            )
            combined_text = "\n\n".join(ocr_texts).strip()
    finally:
        doc.close()

    if not combined_text:
        raise ValueError(
            f"No extractable text found for pages {page_start}–{page_end}. "
            "This chapter may be image-only or require OCR."
        )
    combined_text = combined_text[: max(1000, int(settings.OPENAI_DECOMPOSE_MAX_INPUT_CHARS))]

    chunk_note = (
        f"\nNote: This is pages {page_start}–{page_end} of {total_pages}. "
        "Extract sections only from these pages."
    )
    goal_note = (
        f"\n\nSTUDENT GOAL: \"{objectives_prompt}\"\n"
        "Use this to inform your decomposition:\n"
        "- Break content most relevant to this goal into finer, more precise sections.\n"
        "- Be especially careful with page_start/page_end for those sections.\n"
        "- Prioritise key_concepts that directly serve this goal."
        if objectives_prompt
        else ""
    )
    user_message = (
        DECOMPOSE_PROMPT
        + chunk_note
        + goal_note
        + "\n\nSOURCE TEXT EXTRACT:\n"
        + combined_text
        + "\n\nReturn JSON only."
    )
    raw = _openai_chat_text_sync(
        model=decompose_model,
        system=DECOMPOSE_SYSTEM,
        messages=[{"role": "user", "content": user_message}],
        max_tokens=4000,
        timeout_seconds=settings.OPENAI_DECOMPOSE_TIMEOUT_S,
        max_retries=settings.OPENAI_DECOMPOSE_MAX_RETRIES,
    )
    parsed = _extract_json_object(raw)
    sections = _extract_sections_payload(parsed)
    if not isinstance(sections, list):
        raise ValueError(
            "OpenAI decomposition response did not include a valid sections list. "
            f"Top-level keys: {sorted(parsed.keys())[:12]}"
        )
    return sections


async def _upsert_chapter_lesson(
    *,
    conn: aiosqlite.Connection,
    course_id: str,
    user_id: str,
    chapter_id: str,
    title: str,
    source_pdf_rel: str,
    page_start: int,
    page_end: int,
    sections: list[dict[str, Any]],
) -> str:
    if not sections:
        raise ValueError("Cannot upsert chapter lesson with zero sections.")

    async with conn.execute(
        """SELECT lesson_id FROM course_chapter_lessons
           WHERE course_id = ? AND chapter_id = ?""",
        (course_id, chapter_id),
    ) as cur:
        row = await cur.fetchone()

    lesson_id = str(row[0]) if row else ""
    lesson = await models.get_lesson(conn, lesson_id) if lesson_id else None
    if lesson is None or str(lesson.get("creator_id")) != user_id:
        lesson_id = await models.create_lesson(
            conn,
            creator_id=user_id,
            title=title,
            pdf_path=source_pdf_rel,
            course_id=course_id,
            description=f"Pages {page_start}–{page_end}",
        )
    else:
        await models.update_lesson(
            conn,
            lesson_id,
            title=title,
            description=f"Pages {page_start}–{page_end}",
            course_id=course_id,
            pdf_path=source_pdf_rel,
        )

    await models.upsert_sections(conn, lesson_id, sections)
    await conn.execute(
        """INSERT INTO course_chapter_lessons
           (course_id, chapter_id, lesson_id, created_at, updated_at)
           VALUES (?, ?, ?, datetime('now'), datetime('now'))
           ON CONFLICT(course_id, chapter_id)
           DO UPDATE SET
             lesson_id = excluded.lesson_id,
             updated_at = datetime('now')""",
        (course_id, chapter_id, lesson_id),
    )
    await conn.commit()
    return lesson_id


async def _course_title(conn: aiosqlite.Connection, course_id: str) -> str:
    async with conn.execute("SELECT title FROM courses WHERE id = ?", (course_id,)) as cur:
        row = await cur.fetchone()
    return str(row[0]) if row else "Untitled Course"


async def _load_source(conn: aiosqlite.Connection, course_id: str) -> dict[str, Any]:
    async with conn.execute(
        """SELECT course_id, creator_id, pdf_hash, pdf_path, page_count, toc_json
           FROM course_source_files
           WHERE course_id = ?""",
        (course_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise RuntimeError("No textbook source found for this course.")
    return dict(row)


async def _load_chapters(
    conn: aiosqlite.Connection,
    course_id: str,
    *,
    included_only: bool = False,
) -> list[dict[str, Any]]:
    sql = (
        "SELECT id, idx, title, page_start, page_end, included "
        "FROM course_chapter_drafts WHERE course_id = ?"
    )
    params: list[Any] = [course_id]
    if included_only:
        sql += " AND included = 1"
    sql += " ORDER BY idx"
    async with conn.execute(sql, params) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _write_chapter_drafts(
    conn: aiosqlite.Connection,
    *,
    course_id: str,
    chapters: list[dict[str, Any]],
) -> None:
    await conn.execute(
        "DELETE FROM course_chapter_drafts WHERE course_id = ?", (course_id,)
    )
    for idx, chapter in enumerate(chapters):
        await conn.execute(
            """INSERT INTO course_chapter_drafts
               (id, course_id, idx, title, page_start, page_end, included, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                db.new_id(),
                course_id,
                idx,
                chapter["title"],
                int(chapter["page_start"]),
                int(chapter["page_end"]),
                1 if chapter.get("included", True) else 0,
            ),
        )
