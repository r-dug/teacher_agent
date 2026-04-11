"""Tests for scripts/decompose_model_audit.py.

Plan B follow-up B2: small unit tests for the report writer and the
structural rubric.  No real LLM calls — we test pure functions with
fake input.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

# Add backend/scripts/ to path so we can import decompose_model_audit
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "backend" / "scripts"))

import decompose_model_audit as audit  # type: ignore[import-not-found]


def test_score_output_empty_sections_returns_zero():
    assert audit._score_output([], page_count=10) == 0.0


def test_score_output_reasonable_grid():
    """A well-formed 10-section output covering 20 pages with key_concepts
    and tasks should score close to 1.0."""
    sections = [
        {
            "title": f"Section {i}",
            "content": f"Content {i}",
            "page_start": i * 2 + 1,
            "page_end": i * 2 + 2,
            "key_concepts": ["concept_a", "concept_b"],
            "tasks": [{"type": "concept", "description": "Learn X"}],
        }
        for i in range(10)
    ]
    score = audit._score_output(sections, page_count=20)
    assert score > 0.9


def test_score_output_too_few_sections_halved():
    """1 section gets count_score=0.5 not 1.0."""
    sections = [
        {
            "title": "Only one",
            "content": "content",
            "page_start": 1,
            "page_end": 10,
            "key_concepts": ["a", "b"],
            "tasks": [{"type": "concept", "description": "X"}],
        }
    ]
    score = audit._score_output(sections, page_count=10)
    # count_score = 0.5, coverage = 1.0, kc = 1.0, tasks = 1.0 → avg = 0.875
    assert 0.8 <= score <= 0.9


def test_score_output_no_boundaries_zero_coverage():
    """Sections without page_start/page_end get 0 coverage."""
    sections = [
        {"title": "S1", "content": "c", "key_concepts": ["a", "b"], "tasks": [{}]},
        {"title": "S2", "content": "c", "key_concepts": ["a", "b"], "tasks": [{}]},
        {"title": "S3", "content": "c", "key_concepts": ["a", "b"], "tasks": [{}]},
    ]
    score = audit._score_output(sections, page_count=20)
    # count=1.0, coverage=0.0, kc=1.0, tasks=1.0 → 0.75
    assert 0.7 <= score <= 0.8


def test_estimate_cost_known_model():
    cost = audit._estimate_cost("gpt-4o", input_tokens=1_000_000, output_tokens=1_000_000)
    # Matches the table: $2.50 + $10.00 = $12.50
    assert abs(cost - 12.50) < 0.01


def test_estimate_cost_unknown_model_returns_zero():
    cost = audit._estimate_cost("some-future-model-not-in-table", 100, 100)
    assert cost == 0.0


def test_write_csv_writes_all_fields(tmp_path):
    runs = [
        audit.AuditRun(
            pdf_name="a.pdf",
            model="gpt-4o",
            section_count=5,
            avg_section_words=250.0,
            duration_s=3.2,
            input_tokens=1200,
            output_tokens=800,
            cost_estimate_usd=0.011,
            structural_score=0.85,
            judge_score=0.75,
            aggregate_score=0.80,
        ),
        audit.AuditRun(
            pdf_name="b.pdf",
            model="gpt-4o-mini",
            error="something broke",
        ),
    ]
    out = tmp_path / "report.csv"
    audit._write_csv(runs, out)

    with out.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert rows[0]["model"] == "gpt-4o"
    assert rows[0]["section_count"] == "5"
    assert rows[0]["aggregate_score"] == "0.8"
    assert rows[1]["error"] == "something broke"


def test_write_markdown_groups_by_pdf_and_picks_winner(tmp_path):
    runs = [
        audit.AuditRun(
            pdf_name="textbook.pdf",
            model="gpt-4o",
            section_count=10,
            structural_score=0.9,
            aggregate_score=0.9,
            cost_estimate_usd=0.05,
        ),
        audit.AuditRun(
            pdf_name="textbook.pdf",
            model="gpt-4o-mini",
            section_count=8,
            structural_score=0.75,
            aggregate_score=0.75,
            cost_estimate_usd=0.01,
        ),
    ]
    out = tmp_path / "report.md"
    audit._write_markdown(runs, out)
    text = out.read_text()
    assert "textbook.pdf" in text
    assert "gpt-4o" in text
    assert "gpt-4o-mini" in text
    # Winner is the highest aggregate_score
    assert "Suggested winner**: `gpt-4o`" in text
