import json
from dataclasses import dataclass, field


@dataclass
class Curriculum:
    title: str
    sections: list[dict]
    idx: int = 0
    task_progress: dict[str, list[dict]] = field(default_factory=dict)

    @property
    def current(self) -> dict:
        return self.sections[self.idx]

    @property
    def is_last(self) -> bool:
        return self.idx >= len(self.sections) - 1

    @property
    def progress(self) -> float:
        return self.idx / len(self.sections)

    def current_tasks(self) -> list[dict]:
        """Return checklist for current section, auto-generating from key_concepts if absent."""
        key = str(self.idx)
        if key not in self.task_progress:
            concepts = self.current.get("key_concepts", [])
            tasks = [
                {"concept": c, "status": "pending", "evidence": None}
                for c in concepts
            ]
            tasks.append({
                "concept": (
                    "Section quiz: quiz the student on all key topics from this "
                    "section to confirm overall understanding before advancing. "
                    "If there are any tasks in which they demonstrate weakness, "
                    "unmark them as complete and continue teaching this module."
                ),
                "status": "pending",
                "evidence": None,
            })
            self.task_progress[key] = tasks
        return self.task_progress[key]

    def all_tasks_done(self) -> bool:
        """True if every task for the current section is passed or skipped."""
        return all(t["status"] in ("passed", "skipped") for t in self.current_tasks())

    def mark_task(self, task_idx: int, evidence: str) -> dict | None:
        """Mark a task as passed. Returns the task dict, or None if out of range
        or already passed.

        When marking the quiz (last task), any remaining pending concept tasks
        are auto-passed — the agent reaching the quiz stage is sufficient signal
        that the concepts were covered.
        """
        tasks = self.current_tasks()
        if 0 <= task_idx < len(tasks):
            if tasks[task_idx]["status"] == "passed":
                return tasks[task_idx]  # already done — idempotent success
            # Quiz task (last): auto-pass any remaining pending concepts
            if task_idx == len(tasks) - 1:
                for t in tasks[:-1]:
                    if t["status"] not in ("passed", "skipped"):
                        t["status"] = "passed"
                        t["evidence"] = t.get("evidence") or "auto-passed at quiz"
            tasks[task_idx]["status"] = "passed"
            tasks[task_idx]["evidence"] = evidence
            return tasks[task_idx]
        return None

    def advance(self) -> bool:
        """Advance to the next section. Returns False if already on the last section."""
        if self.is_last:
            return False
        self.idx += 1
        return True

    def unmark_task(self, task_idx: int, reason: str) -> dict | None:
        """Reset a passed task back to pending. Returns the task dict or None."""
        tasks = self.current_tasks()
        if 0 <= task_idx < len(tasks):
            if tasks[task_idx]["status"] != "passed":
                return None  # not passed — nothing to unmark
            tasks[task_idx]["status"] = "pending"
            tasks[task_idx]["evidence"] = None
            return tasks[task_idx]
        return None

    def task_progress_json(self) -> str:
        """Serialize for DB persistence."""
        return json.dumps(self.task_progress)
