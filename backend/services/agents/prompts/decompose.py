"""Prompt constants for the PDF decomposition pipeline."""

from __future__ import annotations

DECOMPOSE_SYSTEM = (
    "You are an expert curriculum designer. Analyze the document and decompose it into "
    "logical teachable sections. Respond with a JSON object only — no explanation, no markdown. "
    "CRITICAL: page_start and page_end must be the exact page numbers where each section "
    "appears in the PDF. Count pages carefully — these numbers are used to display the actual "
    "PDF pages to the student during teaching."
)

DECOMPOSE_PROMPT = """
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
- Return only valid JSON.
"""

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
