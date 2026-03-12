"""Lesson persistence: Curriculum data model and LessonStore I/O."""

from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

LESSONS_DIR = Path.home() / ".local" / "share" / "pdf-to-audio" / "lessons"
PERSONAS_FILE = Path.home() / ".local" / "share" / "pdf-to-audio" / "personas.json"


# ── data model ────────────────────────────────────────────────────────────────

@dataclass
class Curriculum:
    title: str
    sections: list[dict]
    idx: int = 0

    @property
    def current(self) -> dict:
        return self.sections[self.idx]

    @property
    def is_last(self) -> bool:
        return self.idx >= len(self.sections) - 1

    @property
    def progress(self) -> float:
        return self.idx / len(self.sections)


@dataclass
class LessonSnapshot:
    """Everything loaded from a .lesson.json + .audio.npz pair."""
    curriculum: Curriculum
    pdf_path: str
    messages: list[dict]
    completed: bool
    display_log: list[tuple[str, str]]
    chunk_tag_map: dict[str, tuple[int, int]]
    audio_turns: list[list[np.ndarray]]


# ── persistence ───────────────────────────────────────────────────────────────

class LessonStore:
    """Static helpers for saving and loading lesson state to/from disk."""

    @staticmethod
    def lesson_path(pdf_path: str) -> Path:
        h = hashlib.md5(pdf_path.encode()).hexdigest()[:10]
        return LESSONS_DIR / f"{Path(pdf_path).stem}_{h}.lesson.json"

    @staticmethod
    def audio_path(pdf_path: str) -> Path:
        return LessonStore.lesson_path(pdf_path).with_suffix(".audio.npz")

    @staticmethod
    def list_saved() -> list[Path]:
        """Return all *.lesson.json files, newest first."""
        if not LESSONS_DIR.exists():
            return []
        return sorted(LESSONS_DIR.glob("*.lesson.json"),
                      key=lambda p: p.stat().st_mtime, reverse=True)

    @staticmethod
    def save(
        curriculum: Curriculum,
        pdf_path: str,
        messages: list,
        completed: bool,
        display_log: list,
        chunk_tag_map: dict,
        audio_turns: list[list[np.ndarray]],
    ) -> None:
        LESSONS_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 3,
            "pdf_path": pdf_path,
            "curriculum": {
                "title": curriculum.title,
                "sections": curriculum.sections,
                "idx": curriculum.idx,
            },
            "messages": LessonStore.serialize_messages(messages),
            "completed": completed,
            "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "display_log": display_log,
            "chunk_tag_map": chunk_tag_map,
        }
        LessonStore.lesson_path(pdf_path).write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )

        # Companion NPZ for audio chunks
        audio_file = LessonStore.audio_path(pdf_path)
        arrays: dict[str, np.ndarray] = {}
        for t_idx, chunks in enumerate(audio_turns):
            for c_idx, audio in enumerate(chunks):
                if audio.size > 0:
                    arrays[f"t{t_idx}c{c_idx}"] = audio
        if arrays:
            np.savez_compressed(str(audio_file), **arrays)
        elif audio_file.exists():
            audio_file.unlink(missing_ok=True)

    @staticmethod
    def load(lesson_file: Path) -> LessonSnapshot:
        data = json.loads(lesson_file.read_text(encoding="utf-8"))
        c = data["curriculum"]
        curriculum = Curriculum(
            title=c["title"], sections=c["sections"], idx=c["idx"]
        )
        pdf_path: str = data["pdf_path"]
        messages = LessonStore.repair_messages(data.get("messages", []))
        completed: bool = data.get("completed", False)
        display_log: list[tuple[str, str]] = [
            tuple(e) for e in data.get("display_log", [])
        ]
        raw_map = data.get("chunk_tag_map", {})
        chunk_tag_map: dict[str, tuple[int, int]] = {
            k: tuple(v) for k, v in raw_map.items()
        }

        # Restore audio turns from companion NPZ
        audio_turns: list[list[np.ndarray]] = []
        audio_file = LessonStore.audio_path(pdf_path)
        if audio_file.exists():
            npz = np.load(str(audio_file))
            keys = list(npz.files)
            if keys:
                max_t = max(int(k.split("c")[0][1:]) for k in keys)
                audio_turns = [[] for _ in range(max_t + 1)]
                for key in sorted(
                    keys,
                    key=lambda k: (int(k.split("c")[0][1:]), int(k.split("c")[1])),
                ):
                    t_idx = int(key.split("c")[0][1:])
                    audio_turns[t_idx].append(npz[key])

        return LessonSnapshot(
            curriculum=curriculum,
            pdf_path=pdf_path,
            messages=messages,
            completed=completed,
            display_log=display_log,
            chunk_tag_map=chunk_tag_map,
            audio_turns=audio_turns,
        )

    @staticmethod
    def serialize_messages(messages: list) -> list:
        """Convert messages (may contain SDK objects) to plain JSON-safe dicts."""
        result = []
        for msg in messages:
            content = msg["content"]
            if isinstance(content, str):
                result.append({"role": msg["role"], "content": content})
            elif isinstance(content, list):
                serialized = []
                for block in content:
                    if isinstance(block, dict):
                        serialized.append(block)
                    elif hasattr(block, "type") and block.type == "text":
                        serialized.append({"type": "text", "text": block.text})
                    elif hasattr(block, "type") and block.type == "tool_use":
                        serialized.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })
                result.append({"role": msg["role"], "content": serialized})
        return result

    @staticmethod
    def repair_messages(messages: list) -> list:
        """Remove orphaned tool_result blocks that have no matching tool_use id."""
        present_ids: set[str] = set()
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        present_ids.add(block["id"])

        clean: list[dict] = []
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                clean.append(msg)
                continue
            filtered = [
                b for b in content
                if not (
                    isinstance(b, dict)
                    and b.get("type") == "tool_result"
                    and b.get("tool_use_id") not in present_ids
                )
            ]
            if filtered:
                clean.append({"role": msg["role"], "content": filtered})
        return clean

    # ── personas ──────────────────────────────────────────────────────────────

    @staticmethod
    def load_personas() -> dict:
        if PERSONAS_FILE.exists():
            try:
                return json.loads(PERSONAS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    @staticmethod
    def save_personas(personas: dict) -> None:
        PERSONAS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PERSONAS_FILE.write_text(json.dumps(personas, indent=2), encoding="utf-8")
