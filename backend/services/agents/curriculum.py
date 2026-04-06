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
            self.task_progress[key] = [
                {"concept": c, "status": "pending", "evidence": None}
                for c in concepts
            ]
        return self.task_progress[key]

    def all_tasks_done(self) -> bool:
        """True if every task for the current section is passed or skipped."""
        return all(t["status"] in ("passed", "skipped") for t in self.current_tasks())

    def mark_task(self, task_idx: int, evidence: str) -> dict | None:
        """Mark a task as passed. Returns the task dict or None if out of range."""
        tasks = self.current_tasks()
        if 0 <= task_idx < len(tasks):
            tasks[task_idx]["status"] = "passed"
            tasks[task_idx]["evidence"] = evidence
            return tasks[task_idx]
        return None

    def task_progress_json(self) -> str:
        """Serialize for DB persistence."""
        return json.dumps(self.task_progress)
