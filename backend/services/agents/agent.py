"""Abstract base class for all agents."""

from __future__ import annotations

from abc import ABC


class Agent(ABC):
    """Abstract base for all agents.

    All concrete agents operate with a configured model and implement a
    specific capability (teaching, lesson planning, web search, etc.).
    """

    def __init__(self, model: str) -> None:
        self._model = model
