"""
Canonical PDF decomposition module.

Plan B follow-up B3: single source of truth for PDF → curriculum
decomposition.  Replaces the two parallel implementations that used to
live in ``LessonPlannerAgent`` (now deleted) and
``course_authoring._decompose_chapter_openai_sync`` (now folded into
the course-authoring path as a thin wrapper around this module).

Public entry points:

- ``decompose_full_pdf()`` — used by the live WS lesson flow.  Two-phase
  parallel pipeline: discover segment boundaries (TOC / LLM /
  fixed-size), then decompose each segment in parallel via the source
  matching the ``llm_provider``.
- ``decompose_segment()`` — provider-agnostic single-segment dispatcher.
  Routes internally to the OpenAI text path or the Anthropic PDF-blob
  path based on the provider's ``model``.  Used by the course-authoring
  background job (one chapter at a time).
- ``decompose_from_text()`` — used when the segment text has already
  been produced by some external pipeline (e.g. vision-OCR for
  image-only PDFs).  Skips PDF extraction and runs the decompose prompt
  directly through ``LLMProvider.complete()``.

The OpenAI path goes through ``LLMProvider.complete()`` (Responses API
under the hood); the Anthropic PDF-blob path uses the raw SDK client
from ``clients.anthropic_client_for("decompose")`` because it relies on
adaptive thinking + the custom ``search_web`` tool that ``do_turn``'s
abstraction doesn't currently expose.  Both paths return the same
``(title, sections)`` tuple shape.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING

from .config import MAX_SEGMENT_WORKERS, SEGMENT_TARGET_PAGES
from .curriculum import Curriculum
from .message_utils import _block_to_api_dict
from .model_chains import TITLE_CHAIN
from .model_config import build_chain
from .prompts.decompose import (
    DECOMPOSE_PROMPT,
    DECOMPOSE_SYSTEM,
    _SEGMENT_PROMPT_TEMPLATE,
    _SEGMENT_SYSTEM,
)
from .tools import SEARCH_WEB_TOOL

if TYPE_CHECKING:
    from .providers.base import LLMProvider
    from .search_agent import SearchAgent

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Segment discovery (phase 1)
# ─────────────────────────────────────────────────────────────────────────────


def _extract_structural_text(doc) -> str:
    """Return the first text line of each page, prefixed with its 1-based page number."""
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

    Returns None when the TOC is absent, too coarse, or too granular.
    """
    toc = doc.get_toc()  # [(level, title, page_1based), ...]
    if not toc:
        return None

    # Collect only top-level entries (level 1).
    level1 = [(t[1], t[2] - 1) for t in toc if t[0] == 1]
    if len(level1) < 2:
        return None

    segments: list[tuple[int, int]] = []
    for i, (title, start) in enumerate(level1):
        end = level1[i + 1][1] if i + 1 < len(level1) else total_pages
        if end <= start:
            continue
        size = end - start
        # Reject segments that are wildly different from target
        if size > target * 3 or size < max(3, target // 3):
            return None
        segments.append((start, end))

    if not segments:
        return None

    return segments


def _find_segments(doc, total_pages: int) -> tuple[list[tuple[int, int]], str | None]:
    """Phase 1: find natural segment boundaries (TOC → LLM → fixed-size fallback).

    Returns (segments, doc_title) where each segment is a
    (start_0indexed, end_exclusive_0indexed) pair.
    """
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
        provider = build_chain(TITLE_CHAIN)
        raw, _usage = provider.complete(
            system=_SEGMENT_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
        )
        raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
        raw = raw[raw.find("{") : raw.rfind("}") + 1]
        data = json.loads(raw)
        doc_title = data.get("title")

        segs: list[tuple[int, int]] = []
        for s in data.get("segments", []):
            start = max(1, int(s["page_start"])) - 1
            end = min(total_pages, int(s["page_end"]))
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


# ─────────────────────────────────────────────────────────────────────────────
# Per-segment decomposition (phase 2)
# ─────────────────────────────────────────────────────────────────────────────


def _build_goal_note(student_goal: str | None) -> str:
    """Build the 'STUDENT GOAL' prompt suffix shared by all paths."""
    if not student_goal:
        return ""
    return (
        f"\n\nSTUDENT GOAL: \"{student_goal}\"\n"
        "Use this to inform your decomposition:\n"
        "- Break content most relevant to this goal into finer, more precise sections.\n"
        "- Be especially careful with page_start/page_end for those sections.\n"
        "- Prioritise key_concepts that directly serve this goal."
    )


def _build_chunk_note(seg_start: int, seg_end: int, total_pages: int) -> str:
    """Build the chunk-note prompt suffix indicating which pages the
    segment covers.  Empty string when the segment IS the whole doc."""
    if total_pages <= (seg_end - seg_start):
        return ""
    return (
        f"\nNote: This is pages {seg_start + 1}–{seg_end} of {total_pages}. "
        "Extract sections only from these pages."
    )


def _parse_decompose_response(raw: str) -> tuple[str | None, list[dict]]:
    """Parse an LLM decomposition response as JSON.  Handles markdown
    code fences, extra whitespace, and surrounding prose.  Returns
    ``(title, sections)``."""
    if not raw:
        raise ValueError("Decompose agent returned no text block")
    stripped = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    start_idx = stripped.find("{")
    end_idx = stripped.rfind("}")
    if start_idx >= 0 and end_idx > start_idx:
        stripped = stripped[start_idx : end_idx + 1]
    data = json.loads(stripped)
    return data.get("title"), data.get("sections", [])


def decompose_segment(
    *,
    pdf_bytes: bytes,
    seg_start: int,
    seg_end: int,
    total_pages: int,
    llm_provider: "LLMProvider | None" = None,
    student_goal: str | None = None,
    max_input_chars: int = 120_000,
    search_agent: "SearchAgent | None" = None,
    cancel_event: threading.Event | None = None,
    on_progress: Callable[[str], None] | None = None,
    on_token_usage: Callable[[str, str, object], None] | None = None,
) -> tuple[str | None, list[dict]]:
    """Provider-agnostic entry point for one-segment decomposition.

    Plan B follow-up B3: single call that both the live lesson flow and
    the course authoring background job use.  Routes to the appropriate
    provider-specific implementation (OpenAI text path or Anthropic
    PDF-blob path) based on the provider in ``DECOMPOSE_CHAIN``.

    If ``llm_provider`` is None, uses ``build_chain(DECOMPOSE_CHAIN)``.
    Callers can inject a custom provider (e.g., per-call model override)
    by passing one explicitly.

    Returns ``(title, sections)``; title is None when the model doesn't
    emit one.
    """
    if llm_provider is None:
        from .model_chains import DECOMPOSE_CHAIN

        llm_provider = build_chain(DECOMPOSE_CHAIN)

    goal_note = _build_goal_note(student_goal)
    is_openai = _is_openai_model(llm_provider.model) or llm_provider.name == "openai"

    if is_openai:
        return decompose_segment_openai(
            llm_provider=llm_provider,
            pdf_bytes=pdf_bytes,
            seg_start=seg_start,
            seg_end=seg_end,
            total_pages=total_pages,
            goal_note=goal_note,
            max_input_chars=max_input_chars,
            cancel_event=cancel_event,
            on_token_usage=on_token_usage,
        )

    # Anthropic path: raw SDK client via the centralized factory.
    from .clients import anthropic_client_for

    anthropic_client = anthropic_client_for("decompose")
    supports_thinking = "haiku" not in llm_provider.model
    return decompose_segment_anthropic(
        anthropic_client=anthropic_client,
        model=llm_provider.model,
        pdf_bytes=pdf_bytes,
        seg_start=seg_start,
        seg_end=seg_end,
        total_pages=total_pages,
        goal_note=goal_note,
        search_agent=search_agent,
        on_progress=on_progress,
        cancel_event=cancel_event,
        on_token_usage=on_token_usage,
        supports_thinking=supports_thinking,
    )


def decompose_from_text(
    *,
    llm_provider: "LLMProvider | None" = None,
    text: str,
    seg_start: int,
    seg_end: int,
    total_pages: int,
    student_goal: str | None = None,
    max_input_chars: int = 120_000,
    on_token_usage: Callable[[str, str, object], None] | None = None,
) -> tuple[str | None, list[dict]]:
    """Decompose one segment from pre-extracted text.

    Used by callers that have already produced the segment text by some
    means other than PyMuPDF text extraction (e.g., an OCR pipeline for
    image-only PDFs).  Provider-agnostic — routes through the supplied
    ``llm_provider`` (or ``DECOMPOSE_CHAIN``) via the ``complete()``
    method.
    """
    if llm_provider is None:
        from .model_chains import DECOMPOSE_CHAIN

        llm_provider = build_chain(DECOMPOSE_CHAIN)

    combined_text = (text or "").strip()
    if not combined_text:
        return None, []
    combined_text = combined_text[:max_input_chars]

    goal_note = _build_goal_note(student_goal)
    chunk_note = _build_chunk_note(seg_start, seg_end, total_pages)
    prompt = (
        DECOMPOSE_PROMPT
        + chunk_note
        + goal_note
        + "\n\nSOURCE TEXT EXTRACT:\n"
        + combined_text
        + "\n\nReturn JSON only."
    )

    try:
        content_text, usage = llm_provider.complete(
            system=DECOMPOSE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
        )
    except Exception as exc:
        raise RuntimeError(f"Decompose chain complete() failed: {exc}") from exc

    if on_token_usage:
        on_token_usage("decompose_pdf", llm_provider.model, usage)

    return _parse_decompose_response(content_text)


def decompose_segment_openai(
    *,
    llm_provider: "LLMProvider",
    pdf_bytes: bytes,
    seg_start: int,
    seg_end: int,
    total_pages: int,
    goal_note: str = "",
    max_input_chars: int = 120_000,
    cancel_event: threading.Event | None = None,
    on_token_usage: Callable[[str, str, object], None] | None = None,
) -> tuple[str | None, list[dict]]:
    """Decompose one PDF segment via the OpenAI text path.

    Extracts text with PyMuPDF, then delegates to ``decompose_from_text``.
    Returns ``(title, sections)``; ``title`` is None when the model
    doesn't emit one.
    """
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
            text_blocks.append(f"[Page {seg_start + i + 1}]\n{text}")
    finally:
        seg_doc.close()

    combined_text = "\n\n".join(text_blocks).strip()
    if not combined_text:
        return None, []

    # ``decompose_from_text`` builds its own goal_note from student_goal,
    # but we already have one pre-formatted.  Extract the goal string if
    # present (we pass student_goal=None and inline-build instead).
    combined_text = combined_text[:max_input_chars]
    chunk_note = _build_chunk_note(seg_start, seg_end, total_pages)
    prompt = (
        DECOMPOSE_PROMPT
        + chunk_note
        + goal_note
        + "\n\nSOURCE TEXT EXTRACT:\n"
        + combined_text
        + "\n\nReturn JSON only."
    )

    try:
        content_text, usage = llm_provider.complete(
            system=DECOMPOSE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
        )
    except Exception as exc:
        raise RuntimeError(f"OpenAI decompose failed: {exc}") from exc

    if on_token_usage:
        on_token_usage("decompose_pdf", llm_provider.model, usage)

    return _parse_decompose_response(content_text)


def decompose_segment_anthropic(
    *,
    anthropic_client,
    model: str,
    pdf_bytes: bytes,
    seg_start: int,
    seg_end: int,
    total_pages: int,
    goal_note: str = "",
    search_agent: "SearchAgent | None" = None,
    on_progress: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    on_token_usage: Callable[[str, str, object], None] | None = None,
    supports_thinking: bool = True,
) -> tuple[str | None, list[dict]]:
    """Decompose one PDF segment via the Anthropic SDK direct path.

    Uses PDF document content blocks, adaptive thinking (on models that
    support it), and the custom ``search_web`` tool (delegates to
    ``search_agent``) in a manual multi-turn loop.

    **Currently non-functional** in the deployed environment because the
    Anthropic API credit balance is zero.  Preserved so topping up
    credits is a config-only change.  The raw ``anthropic_client`` must
    already be constructed by the caller (typically via
    ``clients.anthropic_client_for("decompose")``).
    """
    pdf_data = base64.standard_b64encode(pdf_bytes).decode("utf-8")
    chunk_note = _build_chunk_note(seg_start, seg_end, total_pages)

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

        with anthropic_client.messages.stream(
            model=model,
            max_tokens=32000,
            **({"thinking": {"type": "adaptive"}} if supports_thinking else {}),
            system=DECOMPOSE_SYSTEM,
            tools=[SEARCH_WEB_TOOL],
            messages=messages,
        ) as stream:
            response = stream.get_final_message()

        if on_token_usage:
            on_token_usage("decompose_pdf", model, response.usage)

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
            return _parse_decompose_response(raw)

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

        search_result = (
            search_agent.search(query)
            if search_agent is not None
            else "Search unavailable. Continue with document content only."
        )
        messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_block.id,
                "content": search_result,
            }],
        })


# ─────────────────────────────────────────────────────────────────────────────
# Full-PDF two-phase pipeline (used by AgentSession.decompose_pdf)
# ─────────────────────────────────────────────────────────────────────────────


def _is_openai_model(model: str) -> bool:
    """Return True if *model* looks like an OpenAI / reasoning model."""
    return model.startswith(("gpt-", "o1", "o3", "o4"))


def decompose_full_pdf(
    *,
    pdf_path: str,
    llm_provider: "LLMProvider",
    search_agent: "SearchAgent | None" = None,
    student_goal: str | None = None,
    on_progress: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    max_input_chars: int = 120_000,
    on_token_usage: Callable[[str, str, object], None] | None = None,
) -> Curriculum:
    """Two-phase parallel PDF → Curriculum pipeline.

    Phase 1: discover segment boundaries (TOC or LLM or fixed chunks).
    Phase 2: decompose each segment in parallel using the appropriate
    source (OpenAI text path or Anthropic PDF-blob path) based on the
    ``llm_provider``'s underlying model.

    Canonical entry point for ``AgentSession.decompose_pdf()``.
    """
    import fitz  # pymupdf

    is_openai = _is_openai_model(llm_provider.model) or llm_provider.name == "openai"
    supports_thinking = "haiku" not in llm_provider.model

    anthropic_client = None
    if not is_openai:
        # Anthropic path — raw SDK client via centralized factory.
        from .clients import anthropic_client_for

        try:
            anthropic_client = anthropic_client_for("decompose")
        except ValueError as exc:
            log.error("Anthropic decompose client unavailable: %s", exc)
            raise

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    goal_note = _build_goal_note(student_goal)

    if on_progress:
        on_progress("Analysing document structure…")

    if is_openai:
        # OpenAI text path: TOC → fixed-size fallback.  Skip the
        # LLM-structural step since the text path runs a per-segment
        # LLM call anyway.
        toc_segs = _toc_segments(doc, total_pages, SEGMENT_TARGET_PAGES)
        if toc_segs:
            segments, doc_title = toc_segs, None
        else:
            segments = [
                (start, min(start + SEGMENT_TARGET_PAGES, total_pages))
                for start in range(0, total_pages, SEGMENT_TARGET_PAGES)
            ]
            doc_title = None
    else:
        segments, doc_title = _find_segments(doc, total_pages)

    n_segs = len(segments)
    if on_progress and n_segs > 1:
        on_progress(f"Found {n_segs} segments — decomposing in parallel…")

    # Extract PDF bytes for each segment while the doc is open.
    segment_bytes: list[bytes] = []
    for start, end in segments:
        chunk = fitz.open()
        chunk.insert_pdf(doc, from_page=start, to_page=end - 1)
        segment_bytes.append(chunk.tobytes())
        chunk.close()
    doc.close()

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
        if is_openai:
            return idx, decompose_segment_openai(
                llm_provider=llm_provider,
                pdf_bytes=pdf_bytes,
                seg_start=start,
                seg_end=end,
                total_pages=total_pages,
                goal_note=goal_note,
                max_input_chars=max_input_chars,
                cancel_event=cancel_event,
                on_token_usage=on_token_usage,
            )
        return idx, decompose_segment_anthropic(
            anthropic_client=anthropic_client,
            model=llm_provider.model,
            pdf_bytes=pdf_bytes,
            seg_start=start,
            seg_end=end,
            total_pages=total_pages,
            goal_note=goal_note,
            search_agent=search_agent,
            on_progress=on_progress,
            cancel_event=cancel_event,
            on_token_usage=on_token_usage,
            supports_thinking=supports_thinking,
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
        pool.shutdown(wait=False, cancel_futures=True)

    all_sections = [s for seg in ordered_sections for s in seg]
    all_sections.sort(key=lambda s: s.get("page_start", 0))

    return Curriculum(
        title=first_llm_title or doc_title or Path(pdf_path).stem,
        sections=all_sections,
    )
