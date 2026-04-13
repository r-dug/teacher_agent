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
        # 3 concepts + 1 quiz task
        assert len(tasks) == 4
        assert all(t["status"] == "pending" for t in tasks)
        assert all(t["evidence"] is None for t in tasks)
        assert [t["concept"] for t in tasks[:3]] == ["Concept A", "Concept B", "Concept C"]

    def test_quiz_task_auto_appended(self):
        c = _make_curriculum()
        tasks = c.current_tasks()
        assert "quiz" in tasks[-1]["concept"].lower()
        assert tasks[-1]["status"] == "pending"

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

    def test_already_passed_is_idempotent(self):
        """Marking an already-passed task succeeds idempotently (no error loop)."""
        c = _make_curriculum()
        c.current_tasks()
        c.mark_task(0, "first pass")
        result = c.mark_task(0, "second pass")
        assert result is not None  # idempotent success
        assert result["status"] == "passed"
        # evidence should not be overwritten
        assert c.current_tasks()[0]["evidence"] == "first pass"

    def test_quiz_auto_passes_pending_concepts(self):
        """Marking the quiz (last task) auto-passes any remaining pending concepts."""
        c = _make_curriculum()
        tasks = c.current_tasks()
        quiz_idx = len(tasks) - 1
        # Don't mark any concepts — go straight to quiz
        result = c.mark_task(quiz_idx, "quiz passed")
        assert result is not None
        assert result["status"] == "passed"
        # All concept tasks should be auto-passed
        for t in tasks[:-1]:
            assert t["status"] == "passed"
            assert t["evidence"] == "auto-passed at quiz"
        assert c.all_tasks_done()

    def test_quiz_preserves_already_passed_evidence(self):
        """Auto-pass doesn't overwrite evidence on concepts already marked."""
        c = _make_curriculum()
        tasks = c.current_tasks()
        c.mark_task(0, "student explained well")
        quiz_idx = len(tasks) - 1
        c.mark_task(quiz_idx, "quiz passed")
        # First concept keeps its original evidence
        assert tasks[0]["evidence"] == "student explained well"
        # Other concepts get auto-pass evidence
        assert tasks[1]["evidence"] == "auto-passed at quiz"


# ── Curriculum.unmark_task() ─────────────────────────────────────────────────

class TestUnmarkTask:
    def test_unmark_resets_to_pending(self):
        c = _make_curriculum()
        c.current_tasks()
        c.mark_task(0, "understood")
        assert c.current_tasks()[0]["status"] == "passed"
        result = c.unmark_task(0, "confused again")
        assert result is not None
        assert result["status"] == "pending"
        assert result["evidence"] is None

    def test_unmark_out_of_range_returns_none(self):
        c = _make_curriculum()
        c.current_tasks()
        assert c.unmark_task(99, "reason") is None

    def test_unmark_pending_returns_none(self):
        c = _make_curriculum()
        c.current_tasks()
        assert c.unmark_task(0, "not even passed") is None

    def test_unmark_breaks_all_tasks_done(self):
        c = _make_curriculum()
        tasks = c.current_tasks()
        for i in range(len(tasks)):
            c.mark_task(i, f"evidence {i}")
        assert c.all_tasks_done() is True
        c.unmark_task(1, "student confused")
        assert c.all_tasks_done() is False


# ── Curriculum.all_tasks_done() ──────────────────────────────────────────────

class TestAllTasksDone:
    def test_false_when_pending(self):
        c = _make_curriculum()
        c.current_tasks()
        assert c.all_tasks_done() is False

    def test_true_when_all_passed(self):
        c = _make_curriculum()
        tasks = c.current_tasks()
        for i in range(len(tasks)):
            c.mark_task(i, f"evidence {i}")
        assert c.all_tasks_done() is True

    def test_true_with_mixed_passed_and_skipped(self):
        c = _make_curriculum()
        tasks = c.current_tasks()
        tasks[0]["status"] = "passed"
        tasks[1]["status"] = "skipped"
        tasks[2]["status"] = "passed"
        tasks[3]["status"] = "passed"  # quiz
        assert c.all_tasks_done() is True

    def test_false_with_quiz_pending(self):
        """All concept tasks done but quiz still pending — cannot advance."""
        c = _make_curriculum()
        tasks = c.current_tasks()
        for i in range(len(tasks) - 1):  # mark all except quiz
            c.mark_task(i, f"evidence {i}")
        assert c.all_tasks_done() is False

    def test_false_with_one_pending(self):
        c = _make_curriculum()
        c.current_tasks()
        c.mark_task(0, "evidence")
        c.mark_task(1, "evidence")
        # task 2 and quiz still pending
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
            {"concept": "Section quiz: ...", "status": "pending", "evidence": None},
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
        assert "[PENDING] 3. Section quiz" in prompt
        assert "mark_task_complete" in prompt
        assert "unmark_task" in prompt

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
        tasks = c.current_tasks()
        for i in range(len(tasks)):
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
        # tasks 1, 2 and quiz still pending
        assert c.all_tasks_done() is False


# ── Section transition ───────────────────────────────────────────────────────

class TestSectionTransition:
    def test_new_section_gets_own_tasks(self):
        c = _make_curriculum()
        # Complete section 0 tasks
        tasks = c.current_tasks()
        for i in range(len(tasks)):
            c.mark_task(i, f"evidence {i}")
        # Advance to section 1
        c.idx = 1
        tasks = c.current_tasks()
        # 1 concept + 1 quiz
        assert len(tasks) == 2
        assert tasks[0]["concept"] == "Concept D"
        assert tasks[0]["status"] == "pending"
        assert "quiz" in tasks[1]["concept"].lower()
        # Section 0 progress is preserved
        assert len(c.task_progress["0"]) == 4
        assert all(t["status"] == "passed" for t in c.task_progress["0"])
