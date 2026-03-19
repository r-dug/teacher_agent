from dataclasses import dataclass


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
