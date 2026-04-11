#!/usr/bin/env python3
"""
Decomposition model audit harness.

Plan B follow-up B2: compare decomposition quality across candidate
models on a set of representative PDFs.  Produces a side-by-side report
so the user can pick the best model for ``DECOMPOSE_CHAIN`` in
``backend/services/agents/model_chains.py``.

**Do not run automatically.**  This script makes real LLM calls that
cost money.  Invoke it manually when you have a representative PDF set
ready and want to compare model options.

Usage
-----

    # Show help
    python -m backend.scripts.decompose_model_audit --help

    # Dry run: loads PDFs, builds the grid, prints what would run — no API calls
    python -m backend.scripts.decompose_model_audit \\
        --pdf storage/courses/aefbcb1d-*.pdf \\
        --pdf storage/courses/3b9fcaac-*.pdf \\
        --model gpt-4o \\
        --model gpt-4o-mini \\
        --dry-run

    # Real run: invoke each (pdf, model) pair and write a CSV + Markdown report
    python -m backend.scripts.decompose_model_audit \\
        --pdf storage/courses/aefbcb1d-*.pdf \\
        --pdf storage/courses/3b9fcaac-*.pdf \\
        --model gpt-4o \\
        --model gpt-4o-mini \\
        --out audit_report

    # Enable LLM-as-judge scoring (more calls, more money, better signal)
    python -m backend.scripts.decompose_model_audit \\
        --pdf storage/courses/aefbcb1d-*.pdf \\
        --model gpt-4o \\
        --model gpt-4o-mini \\
        --out audit_report \\
        --judge gpt-4o

Output
------

``<out>.csv`` — one row per (pdf, model): model_name, pdf_name,
section_count, avg_section_words, duration_s, input_tokens,
output_tokens, cost_estimate_usd, judge_score (if --judge),
error_text (if the run failed).

``<out>.md`` — human-readable summary grouped by PDF, with a "winner"
suggestion based on a weighted combination of cost, latency, and
judge score (if enabled).

Structural rubric
-----------------

Without --judge, the script scores each output on cheap deterministic
signals:

- **section_count_reasonable**: 1.0 if 3 ≤ count ≤ 25, 0.5 if 1-2
  or 26-40, 0.0 otherwise
- **boundaries_cover_pdf**: fraction of pages covered by at least one
  section (1.0 = full coverage, 0.0 = no page boundaries set)
- **key_concepts_present**: fraction of sections with at least 2
  non-empty key_concepts
- **tasks_present**: fraction of sections with at least 1 actionable task

The aggregate score is the mean of the four signals.  With --judge,
an additional LLM-as-judge score is added (mean of the two).

Rubric weights can be tuned in ``_score_output``.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Repo root is two parents up (backend/scripts/ → backend/ → repo root).
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


@dataclass
class AuditRun:
    """One (pdf, model) pair's result."""
    pdf_name: str
    model: str
    section_count: int = 0
    avg_section_words: float = 0.0
    duration_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate_usd: float = 0.0
    structural_score: float = 0.0
    judge_score: float | None = None
    aggregate_score: float = 0.0
    error: str = ""
    raw_sections: list[dict] = field(default_factory=list)


# Rough cost table (USD per 1M tokens).  Update as pricing changes.
# Values as of 2026-04-11; see provider docs for current rates.
_COST_TABLE: dict[str, tuple[float, float]] = {
    # model: (input_per_1m, output_per_1m)
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o-search-preview": (2.50, 10.00),
    "o1": (15.00, 60.00),
    "o3": (10.00, 40.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-opus-4-6": (15.00, 75.00),
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Best-effort cost estimate based on the _COST_TABLE."""
    rates = _COST_TABLE.get(model)
    if rates is None:
        return 0.0
    in_rate, out_rate = rates
    return (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate


def _extract_pdf_pages(pdf_path: Path) -> tuple[int, str]:
    """Return (page_count, combined_text).  Uses PyMuPDF (fitz).

    The combined text is what the decomposer will see for the OpenAI
    text-based path.  For very large PDFs this is truncated by the
    chain's OPENAI_DECOMPOSE_MAX_INPUT_CHARS setting.
    """
    import fitz

    doc = fitz.open(str(pdf_path))
    try:
        page_count = len(doc)
        parts: list[str] = []
        for i, page in enumerate(doc):
            text = page.get_text().strip()
            if text:
                parts.append(f"[Page {i + 1}]\n{text}")
        return page_count, "\n\n".join(parts)
    finally:
        doc.close()


def _run_one(
    pdf_path: Path,
    model: str,
    max_input_chars: int,
    dry_run: bool,
) -> AuditRun:
    """Run decomposition against one (pdf, model) pair.

    Uses ``OpenAILLMProvider.complete()`` directly (bypassing
    ``DECOMPOSE_CHAIN``) so we can swap the model per-call without
    mutating global state.
    """
    run = AuditRun(pdf_name=pdf_path.name, model=model)

    try:
        page_count, combined_text = _extract_pdf_pages(pdf_path)
        if not combined_text:
            run.error = "no extractable text (image-only PDF?)"
            return run

        if len(combined_text) > max_input_chars:
            combined_text = combined_text[:max_input_chars]

        if dry_run:
            # Dry run: don't even import the provider or check the API key.
            # We just validated the PDF parses and fits in max_input_chars.
            run.error = f"DRY RUN — would send {len(combined_text)} chars to {model}"
            return run

        from backend.services.agents.prompts.decompose import (
            DECOMPOSE_PROMPT,
            DECOMPOSE_SYSTEM,
        )
        from backend.services.agents.providers.openai import OpenAILLMProvider

        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            run.error = "OPENAI_API_KEY not set"
            return run

        user_message = (
            DECOMPOSE_PROMPT
            + f"\nNote: This is a full document of {page_count} pages.  "
            + "Extract top-level sections from the entire document."
            + "\n\nSOURCE TEXT EXTRACT:\n"
            + combined_text
            + "\n\nReturn JSON only."
        )

        provider = OpenAILLMProvider(
            model=model,
            api_key=api_key,
            timeout_seconds=60.0,
            max_retries=1,
        )

        t0 = time.monotonic()
        raw_text, usage = provider.complete(
            system=DECOMPOSE_SYSTEM,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=4000,
        )
        run.duration_s = time.monotonic() - t0
        run.input_tokens = getattr(usage, "input_tokens", 0) or 0
        run.output_tokens = getattr(usage, "output_tokens", 0) or 0
        run.cost_estimate_usd = _estimate_cost(model, run.input_tokens, run.output_tokens)

        # Parse sections
        import re

        stripped = re.sub(r"```(?:json)?\s*", "", raw_text).strip().rstrip("`").strip()
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            stripped = stripped[start : end + 1]
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            run.error = f"JSON parse failed: {exc}"
            return run

        sections = parsed.get("sections", []) if isinstance(parsed, dict) else []
        if not isinstance(sections, list):
            run.error = "no sections list in response"
            return run

        run.raw_sections = sections
        run.section_count = len(sections)
        if sections:
            total_words = sum(
                len(str(s.get("content", "")).split()) for s in sections
            )
            run.avg_section_words = total_words / len(sections)

        run.structural_score = _score_output(sections, page_count)
        run.aggregate_score = run.structural_score  # judge may bump this later

    except Exception as exc:
        run.error = f"{type(exc).__name__}: {exc}"

    return run


def _score_output(sections: list[dict], page_count: int) -> float:
    """Deterministic structural rubric (see module docstring)."""
    if not sections:
        return 0.0

    # (1) section count reasonable
    n = len(sections)
    if 3 <= n <= 25:
        count_score = 1.0
    elif 1 <= n <= 2 or 26 <= n <= 40:
        count_score = 0.5
    else:
        count_score = 0.0

    # (2) boundaries cover the PDF
    covered_pages: set[int] = set()
    for sec in sections:
        ps = sec.get("page_start")
        pe = sec.get("page_end")
        if isinstance(ps, int) and isinstance(pe, int) and 1 <= ps <= pe <= page_count:
            covered_pages.update(range(ps, pe + 1))
    coverage_score = len(covered_pages) / page_count if page_count > 0 else 0.0

    # (3) key_concepts present
    kc_present = sum(
        1 for s in sections
        if isinstance(s.get("key_concepts"), list) and len(s["key_concepts"]) >= 2
    )
    kc_score = kc_present / len(sections)

    # (4) tasks present
    tasks_present = sum(
        1 for s in sections
        if isinstance(s.get("tasks"), list) and len(s["tasks"]) >= 1
    )
    tasks_score = tasks_present / len(sections)

    return (count_score + coverage_score + kc_score + tasks_score) / 4.0


_JUDGE_PROMPT = """\
You are evaluating a textbook decomposition output for quality.

The decomposer was given a textbook PDF and asked to break it into
self-contained sections, each with a title, content summary, key
concepts, and actionable tasks.  Judge the output's overall quality.

CANDIDATE SECTIONS (JSON):
{sections_json}

Rate the output on a 1-10 scale considering:
- Are the sections coherent and self-contained?
- Are the key concepts concrete and testable?
- Are the tasks actionable for a student?
- Are the section boundaries reasonable (neither too coarse nor too granular)?
- Does the content accurately reflect textbook-style structure?

Respond with ONLY a single number from 1 to 10 and nothing else.
"""


def _run_judge(run: AuditRun, judge_model: str) -> float | None:
    """Call an LLM judge to score the decomposition output."""
    if not run.raw_sections:
        return None
    try:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            return None
        from backend.services.agents.providers.openai import OpenAILLMProvider

        judge = OpenAILLMProvider(
            model=judge_model,
            api_key=api_key,
            timeout_seconds=30.0,
            max_retries=1,
        )
        sections_json = json.dumps(run.raw_sections, indent=2)[:15_000]
        prompt = _JUDGE_PROMPT.format(sections_json=sections_json)
        text, _usage = judge.complete(
            system="You are a rigorous textbook-decomposition quality judge.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=16,
        )
        # Parse first number in the response
        import re

        match = re.search(r"\d+", text.strip())
        if not match:
            return None
        raw = int(match.group())
        return max(0.0, min(1.0, raw / 10.0))
    except Exception:
        return None


def _write_csv(runs: list[AuditRun], path: Path) -> None:
    fieldnames = [
        "pdf_name", "model", "section_count", "avg_section_words",
        "duration_s", "input_tokens", "output_tokens", "cost_estimate_usd",
        "structural_score", "judge_score", "aggregate_score", "error",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for run in runs:
            row = {k: v for k, v in asdict(run).items() if k != "raw_sections"}
            writer.writerow(row)


def _write_markdown(runs: list[AuditRun], path: Path) -> None:
    lines: list[str] = []
    lines.append("# Decompose Model Audit\n")
    lines.append(f"Ran {len(runs)} (pdf, model) pair(s).\n")

    # Group by PDF
    by_pdf: dict[str, list[AuditRun]] = {}
    for run in runs:
        by_pdf.setdefault(run.pdf_name, []).append(run)

    for pdf_name, pdf_runs in by_pdf.items():
        lines.append(f"\n## {pdf_name}\n")
        lines.append("| Model | Sections | Avg words | Duration (s) | In / Out tokens | Cost USD | Struct | Judge | Agg |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for run in sorted(pdf_runs, key=lambda r: -r.aggregate_score):
            judge_str = f"{run.judge_score:.2f}" if run.judge_score is not None else "—"
            cost_str = f"${run.cost_estimate_usd:.4f}"
            err_marker = " ❌" if run.error else ""
            lines.append(
                f"| {run.model}{err_marker} | {run.section_count} | "
                f"{run.avg_section_words:.0f} | {run.duration_s:.1f} | "
                f"{run.input_tokens} / {run.output_tokens} | {cost_str} | "
                f"{run.structural_score:.2f} | {judge_str} | {run.aggregate_score:.2f} |"
            )
            if run.error:
                lines.append(f"  - *{run.error}*")

        # Winner suggestion
        successful = [r for r in pdf_runs if not r.error and r.aggregate_score > 0]
        if successful:
            winner = max(successful, key=lambda r: r.aggregate_score)
            lines.append(f"\n**Suggested winner**: `{winner.model}` "
                         f"(aggregate {winner.aggregate_score:.2f}, "
                         f"cost ${winner.cost_estimate_usd:.4f}, "
                         f"{winner.duration_s:.1f}s)")

    path.write_text("\n".join(lines))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compare decomposition quality across candidate LLM models.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--pdf",
        action="append",
        required=True,
        metavar="PATH",
        help="PDF path to audit.  May be passed multiple times for multi-PDF audits.",
    )
    p.add_argument(
        "--model",
        action="append",
        required=True,
        metavar="NAME",
        help="Candidate OpenAI model name (e.g., gpt-4o, gpt-4o-mini).  Pass multiple times.",
    )
    p.add_argument(
        "--out",
        default="audit_report",
        metavar="BASENAME",
        help="Output filename prefix.  Writes <out>.csv and <out>.md.  Default: audit_report",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Load PDFs and build the grid without making any LLM calls.",
    )
    p.add_argument(
        "--max-input-chars",
        type=int,
        default=120_000,
        metavar="N",
        help="Truncate extracted PDF text to this many chars before sending.  Default: 120000",
    )
    p.add_argument(
        "--judge",
        metavar="MODEL",
        help="Optional LLM-as-judge model (e.g. gpt-4o).  Adds a quality score from 0.0 to 1.0.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    pdfs: list[Path] = []
    for pattern in args.pdf:
        p = Path(pattern)
        if p.exists():
            pdfs.append(p)
        else:
            # Try glob-expansion for shells that didn't
            from glob import glob

            matches = [Path(m) for m in glob(pattern)]
            if not matches:
                print(f"[audit] no file matches {pattern!r}", file=sys.stderr)
                return 2
            pdfs.extend(matches)

    if not pdfs:
        print("[audit] no PDFs found", file=sys.stderr)
        return 2

    grid = [(pdf, model) for pdf in pdfs for model in args.model]
    print(f"[audit] grid: {len(grid)} run(s) — {len(pdfs)} PDF(s) × {len(args.model)} model(s)")
    if args.dry_run:
        print("[audit] DRY RUN — no API calls will be made\n")

    runs: list[AuditRun] = []
    for idx, (pdf, model) in enumerate(grid, start=1):
        print(f"[{idx}/{len(grid)}] {pdf.name} / {model} ... ", end="", flush=True)
        run = _run_one(pdf, model, args.max_input_chars, args.dry_run)
        if run.error:
            print(f"FAIL: {run.error}")
        else:
            extras = f" (sections={run.section_count}, duration={run.duration_s:.1f}s)"
            print(f"OK{extras}")
        runs.append(run)

    # Optional LLM-as-judge pass
    if args.judge and not args.dry_run:
        print(f"\n[audit] running LLM judge ({args.judge}) on {len(runs)} run(s)...")
        for run in runs:
            if run.error:
                continue
            score = _run_judge(run, args.judge)
            run.judge_score = score
            if score is not None:
                # Blend structural + judge 50/50
                run.aggregate_score = (run.structural_score + score) / 2.0

    out_prefix = Path(args.out)
    csv_path = out_prefix.with_suffix(".csv")
    md_path = out_prefix.with_suffix(".md")
    _write_csv(runs, csv_path)
    _write_markdown(runs, md_path)
    print(f"\n[audit] wrote {csv_path} and {md_path}")

    # Exit code: non-zero if every run errored
    any_ok = any(not r.error for r in runs)
    return 0 if any_ok else 1


if __name__ == "__main__":
    sys.exit(main())
