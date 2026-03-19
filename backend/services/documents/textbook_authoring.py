"""Helpers for textbook authoring: TOC extraction and chapter draft shaping."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


DEFAULT_FALLBACK_CHAPTER_PAGES = 25


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def infer_chapter_drafts_from_pdf(
    pdf_path: Path, *, fallback_pages: int = DEFAULT_FALLBACK_CHAPTER_PAGES
) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Return (page_count, toc_entries, chapter_drafts) from a textbook PDF.

    toc_entries are raw-ish level/title/page triples.
    chapter_drafts are normalised chapter rows with title/page_start/page_end/included.
    """
    import fitz

    doc = fitz.open(str(pdf_path))
    try:
        total_pages = len(doc)
        if total_pages <= 0:
            raise ValueError("PDF has no pages")

        raw_toc = doc.get_toc() or []
        toc_entries: list[dict[str, Any]] = []
        for row in raw_toc:
            if len(row) < 3:
                continue
            try:
                level = int(row[0])
                title = " ".join(str(row[1] or "").strip().split())
                page = int(row[2])
            except Exception:
                continue
            if page < 1 or page > total_pages:
                continue
            toc_entries.append(
                {
                    "level": level,
                    "title": title or f"Section {page}",
                    "page": page,
                }
            )

        chapters = _chapters_from_toc_entries(toc_entries, total_pages, fallback_pages)
        return total_pages, toc_entries, chapters
    finally:
        doc.close()


def normalize_chapter_drafts(
    chapters: list[dict[str, Any]],
    *,
    page_count: int,
) -> list[dict[str, Any]]:
    """Validate and normalise editable chapter draft payload."""
    if page_count <= 0:
        raise ValueError("Invalid page_count for textbook")

    out: list[dict[str, Any]] = []
    for idx, chapter in enumerate(chapters):
        title = " ".join(str(chapter.get("title", "")).strip().split())
        if not title:
            raise ValueError(f"Chapter {idx + 1} is missing title")
        try:
            page_start = int(chapter.get("page_start"))
            page_end = int(chapter.get("page_end"))
        except Exception as exc:
            raise ValueError(f"Chapter '{title}' has invalid page range") from exc
        if page_start < 1 or page_end < page_start or page_end > page_count:
            raise ValueError(
                f"Chapter '{title}' page range must satisfy 1 <= start <= end <= {page_count}"
            )
        out.append(
            {
                "title": title,
                "page_start": page_start,
                "page_end": page_end,
                "included": bool(chapter.get("included", True)),
            }
        )

    # Ensure chapters are ordered and non-overlapping after edit.
    ordered = sorted(out, key=lambda c: (int(c["page_start"]), int(c["page_end"])))
    for i in range(1, len(ordered)):
        prev = ordered[i - 1]
        cur = ordered[i]
        if int(cur["page_start"]) <= int(prev["page_end"]):
            raise ValueError(
                f"Chapter ranges overlap: '{prev['title']}' and '{cur['title']}'"
            )
    return out


def _chapters_from_toc_entries(
    toc_entries: list[dict[str, Any]],
    total_pages: int,
    fallback_pages: int,
) -> list[dict[str, Any]]:
    starts = _chapter_starts_from_toc(toc_entries)
    if not starts:
        return _fallback_chapters(total_pages, fallback_pages)

    chapters: list[dict[str, Any]] = []
    if starts[0]["page"] > 1:
        chapters.append(
            {
                "title": "Front Matter",
                "page_start": 1,
                "page_end": starts[0]["page"] - 1,
                "included": True,
            }
        )

    for i, item in enumerate(starts):
        page_start = int(item["page"])
        next_start = int(starts[i + 1]["page"]) if i + 1 < len(starts) else (total_pages + 1)
        page_end = next_start - 1
        if page_end < page_start:
            continue
        title = " ".join(str(item.get("title", "")).strip().split()) or f"Chapter {i + 1}"
        chapters.append(
            {
                "title": title,
                "page_start": page_start,
                "page_end": page_end,
                "included": True,
            }
        )

    return chapters or _fallback_chapters(total_pages, fallback_pages)


def _chapter_starts_from_toc(toc_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not toc_entries:
        return []
    levels = sorted({int(e["level"]) for e in toc_entries})
    selected: list[dict[str, Any]] = []

    for level in levels:
        candidates = [e for e in toc_entries if int(e["level"]) == level]
        deduped = _dedupe_by_page(candidates)
        if len(deduped) >= 2:
            selected = deduped
            break

    if not selected:
        selected = _dedupe_by_page(toc_entries)
    return sorted(selected, key=lambda e: int(e["page"]))


def _dedupe_by_page(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_pages: set[int] = set()
    out: list[dict[str, Any]] = []
    for entry in sorted(entries, key=lambda e: int(e["page"])):
        page = int(entry["page"])
        if page in seen_pages:
            continue
        seen_pages.add(page)
        out.append(entry)
    return out


def _fallback_chapters(total_pages: int, chunk_size: int) -> list[dict[str, Any]]:
    size = max(5, int(chunk_size))
    chapters: list[dict[str, Any]] = []
    idx = 1
    for start in range(1, total_pages + 1, size):
        end = min(total_pages, start + size - 1)
        chapters.append(
            {
                "title": f"Part {idx}",
                "page_start": start,
                "page_end": end,
                "included": True,
            }
        )
        idx += 1
    return chapters
