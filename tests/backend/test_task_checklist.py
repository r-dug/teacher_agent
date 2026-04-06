"""Unit tests for the per-section task checklist feature."""

from __future__ import annotations

import json

import pytest

from backend.db.models import parse_task_progress
from backend.services.agents.curriculum import Curriculum
from backend.services.agents.prompts.teaching import make_teaching_prompt


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_curriculum(
    key_concepts: list[str] | None = None,
    task_progress: dict | None = None,
) -> Curriculum:
    """Helper to build a Curriculum with one section."""
    concepts = key_concepts or ["Concept A", "Concept B", "Concept C"]
    return Curriculum(
        title="Test Lesson",
        sections=[
            {
                "title": "Section 1",
                "content": "Content for section 1.",
                "key_concepts": concepts,
                "page_start": 1,
                "page_end": 2,
            },
            {
                "title": "Section 2",
                "content": "Content for section 2.",
                "key_concepts": ["Concept D"],
                "page_start": 3,
                "page_end": 4,
            },
        ],
        idx=0,
        task_progress=task_progress or {},
    )


# ── Curriculum.current_tasks() ───────────────────────────────────────────────

class TestCurrentTasks:
    def test_auto_generates_from_key_concepts(self):
        c = _make_curriculum()
        tasks = c.current_tasks()
        assert len(tasks) == 3
        assert all(t["status"] == "pending" for t in tasks)
        assert all(t["evidence"] is None for t in tasks)
        assert [t["concept"] for t in tasks] == ["Concept A", "Concept B", "Concept C"]

    def test_idempotent(self):
        c = _make_curriculum()
        first = c.current_tasks()
        second = c.current_tasks()
        assert first is second  # same object, not regenerated

    def test_uses_existing_progress(self):
        existing = {
            "0": [
                {"concept": "Concept A", "status": "passed", "evidence": "Got it"},
                {"concept": "Concept B", "status": "pending", "evidence": None},
                {"concept": "Concept C", "status": "pending", "evidence": None},
            ]
        }
        c = _make_curriculum(task_progress=existing)
        tasks = c.current_tasks()
        assert tasks[0]["status"] == "passed"
        assert tasks[1]["status"] == "pending"


# ── Curriculum.mark_task() ───────────────────────────────────────────────────

class TestMarkTask:
    def test_marks_task_passed(self):
        c = _make_curriculum()
        c.current_tasks()  # auto-generate
        result = c.mark_task(1, "Student explained it well")
        assert result is not None
        assert result["status"] == "passed"
        assert result["evidence"] == "Student explained it well"
        assert result["concept"] == "Concept B"

    def test_out_of_range_returns_none(self):
        c = _make_curriculum()
        c.current_tasks()
        assert c.mark_task(99, "evidence") is None
        assert c.mark_task(-1, "evidence") is None


# ── Curriculum.all_tasks_done() ──────────────────────────────────────────────

class TestAllTasksDone:
    def test_false_when_pending(self):
        c = _make_curriculum()
        c.current_tasks()
        assert c.all_tasks_done() is False

    def test_true_when_all_passed(self):
        c = _make_curriculum()
        c.current_tasks()
        for i in range(3):
            c.mark_task(i, f"evidence {i}")
        assert c.all_tasks_done() is True

    def test_true_with_mixed_passed_and_skipped(self):
        c = _make_curriculum()
        tasks = c.current_tasks()
        tasks[0]["status"] = "passed"
        tasks[1]["status"] = "skipped"
        tasks[2]["status"] = "passed"
        assert c.all_tasks_done() is True

    def test_false_with_one_pending(self):
        c = _make_curriculum()
        c.current_tasks()
        c.mark_task(0, "evidence")
        c.mark_task(1, "evidence")
        # task 2 still pending
        assert c.all_tasks_done() is False


# ── JSON round-trip ──────────────────────────────────────────────────────────

class TestTaskProgressSerialization:
    def test_round_trip(self):
        c = _make_curriculum()
        c.current_tasks()
        c.mark_task(0, "understood")

        serialized = c.task_progress_json()
        restored = parse_task_progress(serialized)
        assert restored == c.task_progress

    def test_parse_empty_string(self):
        assert parse_task_progress("") == {}

    def test_parse_none(self):
        assert parse_task_progress(None) == {}

    def test_parse_invalid_json(self):
        assert parse_task_progress("not json") == {}

    def test_parse_valid_empty_object(self):
        assert parse_task_progress("{}") == {}


# ── Prompt injection ─────────────────────────────────────────────────────────

class TestPromptChecklist:
    def test_renders_checklist_with_statuses(self):
        tasks = [
            {"concept": "Concept A", "status": "passed", "evidence": "ok"},
            {"concept": "Concept B", "status": "pending", "evidence": None},
            {"concept": "Concept C", "status": "skipped", "evidence": None},
        ]
        sections = [{
            "title": "S1",
            "content": "Content",
            "key_concepts": ["Concept A", "Concept B", "Concept C"],
            "page_start": 1,
            "page_end": 2,
        }]
        prompt = make_teaching_prompt("Lesson", sections, 0, current_tasks=tasks)
        assert "CONCEPT CHECKLIST" in prompt
        assert "[DONE] 0. Concept A" in prompt
        assert "[PENDING] 1. Concept B" in prompt
        assert "[DONE] 2. Concept C" in prompt  # skipped shows as DONE
        assert "mark_task_complete" in prompt

    def test_falls_back_without_tasks(self):
        sections = [{
            "title": "S1",
            "content": "Content",
            "key_concepts": ["Concept A"],
            "page_start": 1,
            "page_end": 2,
        }]
        prompt = make_teaching_prompt("Lesson", sections, 0)
        assert "KEY CONCEPTS TO VERIFY" in prompt
        assert "- Concept A" in prompt


# ── Advance gate ─────────────────────────────────────────────────────────────

class TestAdvanceGate:
    def test_all_tasks_done_allows_advance(self):
        c = _make_curriculum()
        c.current_tasks()
        for i in range(3):
            c.mark_task(i, f"evidence {i}")
        assert c.all_tasks_done() is True
        # advance would proceed
        old_idx = c.idx
        c.idx += 1
        assert c.idx == old_idx + 1

    def test_incomplete_tasks_blocks_advance(self):
        c = _make_curriculum()
        c.current_tasks()
        c.mark_task(0, "evidence")
        # tasks 1 and 2 still pending
        assert c.all_tasks_done() is False


# ── Section transition ───────────────────────────────────────────────────────

class TestSectionTransition:
    def test_new_section_gets_own_tasks(self):
        c = _make_curriculum()
        # Complete section 0 tasks
        c.current_tasks()
        for i in range(3):
            c.mark_task(i, f"evidence {i}")
        # Advance to section 1
        c.idx = 1
        tasks = c.current_tasks()
        assert len(tasks) == 1
        assert tasks[0]["concept"] == "Concept D"
        assert tasks[0]["status"] == "pending"
        # Section 0 progress is preserved
        assert len(c.task_progress["0"]) == 3
        assert all(t["status"] == "passed" for t in c.task_progress["0"])
