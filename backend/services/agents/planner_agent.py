"""LessonPlannerAgent — two-phase parallel PDF-to-Curriculum pipeline."""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .agent import Agent
from .config import MAX_SEGMENT_WORKERS, SEGMENT_TARGET_PAGES, _HAIKU_MODEL
from .curriculum import Curriculum
from .message_utils import _block_to_api_dict
from .prompts.decompose import (
    DECOMPOSE_PROMPT,
    DECOMPOSE_SYSTEM,
    _SEGMENT_PROMPT_TEMPLATE,
    _SEGMENT_SYSTEM,
)
from .prompts.search import SEARCH_GUARDRAIL_SYSTEM  # noqa: F401 — re-exported for backwards compat
from .tools import SEARCH_WEB_TOOL

log = logging.getLogger(__name__)


# ── Phase 1 helpers (module-level, pure) ──────────────────────────────────────

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

    level1_pages = sorted({page - 1 for level, _, page in toc if level == 1})
    if len(level1_pages) < 2:
        return None

    segments = [
        (level1_pages[i], level1_pages[i + 1] if i + 1 < len(level1_pages) else total_pages)
        for i in range(len(level1_pages))
    ]

    if any((end - start) > target * 3 for start, end in segments):
        return None  # too coarse
    if any((end - start) < 5 for start, end in segments):
        return None  # too granular

    return segments


def _find_segments(
    doc,
    client,
    total_pages: int,
) -> tuple[list[tuple[int, int]], str | None]:
    """Phase 1: find natural segment boundaries (TOC → LLM → fixed-size fallback).

    Returns (segments, doc_title) where each segment is a
    (start_0indexed, end_exclusive_0indexed) pair.
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


# ── LessonPlannerAgent ─────────────────────────────────────────────────────────

class LessonPlannerAgent(Agent):
    """
    Converts a PDF into a Curriculum via a two-phase parallel pipeline.

    Phase 1 (serial, cheap): find natural segment boundaries.
    Phase 2 (parallel): analyse each segment with the configured LLM.

    Uses the Anthropic client directly (not via LLMProvider) because it needs
    document content blocks, the web-search beta, and adaptive thinking — features
    that don't fit the generic do_turn() interface.
    """

    def __init__(
        self,
        decompose_llm_provider: str,
        openai_api_key: str | None,
        openai_timeout_seconds: float,
        openai_max_retries: int,
        openai_max_input_chars: int,
        model: str,
        search_agent: "SearchAgent | None" = None,  # noqa: F821
        on_token_usage: Callable[[str, str, object], None] | None = None,
    ) -> None:
        super().__init__(model)
        self._provider = decompose_llm_provider
        self._openai_api_key = openai_api_key
        self._openai_timeout = openai_timeout_seconds
        self._openai_max_retries = openai_max_retries
        self._openai_max_input_chars = openai_max_input_chars
        self._on_token_usage = on_token_usage
        self._supports_thinking = "haiku" not in model
        self._search_agent = search_agent

    def decompose(
        self,
        pdf_path: str,
        on_progress: Callable[[str], None] | None = None,
        student_goal: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Curriculum:
        """
        Decompose a PDF into a Curriculum.  Synchronous — call from a background thread.
        """
        import fitz  # pymupdf

        anthropic_client = None
        doc = fitz.open(pdf_path)
        total_pages = len(doc)

        if self._provider != "openai":
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

        # Phase 1
        if on_progress:
            on_progress("Analysing document structure…")

        if self._provider == "openai":
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
            segments, doc_title = _find_segments(doc, anthropic_client, total_pages)

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

        # Phase 2 — parallel
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
            if self._provider == "openai":
                return idx, self._decompose_segment_openai(
                    pdf_bytes, start, end, total_pages, goal_note, cancel_event
                )
            return idx, self._decompose_segment(
                anthropic_client, pdf_bytes, start, end, total_pages,
                goal_note, on_progress, cancel_event
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

    # ── private ────────────────────────────────────────────────────────────────

    def _decompose_segment(
        self,
        client,
        pdf_bytes: bytes,
        seg_start: int,
        seg_end: int,
        total_pages: int,
        goal_note: str,
        on_progress: Callable[[str], None] | None,
        cancel_event: threading.Event | None,
    ) -> tuple[str | None, list[dict]]:
        """Analyse one PDF segment via Anthropic (Sonnet + thinking + web search)."""
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
                model=self._model,
                max_tokens=32000,
                **({"thinking": {"type": "adaptive"}} if self._supports_thinking else {}),
                system=DECOMPOSE_SYSTEM,
                tools=[SEARCH_WEB_TOOL],
                messages=messages,
            ) as stream:
                response = stream.get_final_message()

            if self._on_token_usage:
                self._on_token_usage("decompose_pdf", self._model, response.usage)

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

            search_result = (
                self._search_agent.search(query)
                if self._search_agent is not None
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

    def _decompose_segment_openai(
        self,
        pdf_bytes: bytes,
        seg_start: int,
        seg_end: int,
        total_pages: int,
        goal_note: str,
        cancel_event: threading.Event | None,
    ) -> tuple[str | None, list[dict]]:
        """OpenAI fallback: extract text from the segment and ask for JSON decomposition."""
        import fitz
        import httpx

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
        combined_text = combined_text[: self._openai_max_input_chars]

        chunk_note = (
            f"\nNote: This is pages {seg_start + 1}–{seg_end} of {total_pages}. "
            "Extract sections only from these pages."
            if total_pages > (seg_end - seg_start) else ""
        )
        prompt = (
            DECOMPOSE_PROMPT
            + chunk_note
            + goal_note
            + "\n\nSOURCE TEXT EXTRACT:\n"
            + combined_text
            + "\n\nReturn JSON only."
        )

        payload = {
            "model": self._model,
            "max_tokens": 2048,
            "messages": [
                {"role": "system", "content": DECOMPOSE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
        }
        headers = {"Authorization": f"Bearer {self._openai_api_key}"}
        url = "https://api.openai.com/v1/chat/completions"

        last_err: Exception | None = None
        for attempt in range(self._openai_max_retries + 1):
            try:
                with httpx.Client(timeout=self._openai_timeout) as client:
                    resp = client.post(url, headers=headers, json=payload)
                if resp.status_code >= 400:
                    raise RuntimeError(f"{resp.status_code} {resp.text[:300]}")
                data = resp.json()
                choice = (data.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                content_text = str(message.get("content") or "").strip()
                usage = data.get("usage") or {}
                if self._on_token_usage:
                    from types import SimpleNamespace
                    usage_obj = SimpleNamespace(
                        input_tokens=int(usage.get("prompt_tokens") or 0),
                        output_tokens=int(usage.get("completion_tokens") or 0),
                        cache_read_input_tokens=0,
                        cache_creation_input_tokens=0,
                    )
                    self._on_token_usage("decompose_pdf", self._model, usage_obj)
                break
            except Exception as exc:
                last_err = exc
                if attempt >= self._openai_max_retries:
                    raise RuntimeError(f"OpenAI decompose failed: {last_err}") from last_err
                time.sleep(min(0.2 * (2**attempt), 1.0))

        raw = content_text.strip()
        if not raw:
            raise ValueError("OpenAI decompose agent returned no text block")
        raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
        start_idx = raw.find("{")
        end_idx = raw.rfind("}")
        if start_idx >= 0 and end_idx > start_idx:
            raw = raw[start_idx : end_idx + 1]
        parsed = json.loads(raw)
        return parsed.get("title"), parsed.get("sections", [])
