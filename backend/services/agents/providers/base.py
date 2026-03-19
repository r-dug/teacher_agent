"""Abstract base for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace


@dataclass
class LLMTurnResult:
    """Normalised result returned by every LLMProvider.do_turn() implementation."""

    content_blocks: list[dict]          # normalized via _block_to_api_dict
    content_text: str                   # concatenation of text blocks
    tool_use: SimpleNamespace | None    # first tool-use block, or None
    usage: object                       # SDK Usage object or duck-typed equivalent


class LLMProvider(ABC):
    """Contract that every LLM backend must satisfy."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name (e.g. 'anthropic', 'openai')."""
        ...

    @abstractmethod
    def do_turn(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        on_text_chunk: Callable[[str], None] | None = None,
    ) -> LLMTurnResult:
        """Execute one LLM turn and return a normalised result.

        Args:
            model: Model identifier string.
            system: System prompt.
            messages: Conversation history in Anthropic wire format.
            tools: List of tool schemas in Anthropic format.
            on_text_chunk: Optional callback fired per streamed text delta.

        Returns:
            LLMTurnResult with all content blocks normalised to plain dicts.
        """
        ...
