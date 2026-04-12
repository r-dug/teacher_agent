"""Abstract base for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class LLMTurnResult:
    """Normalised result returned by every LLMProvider.do_turn() implementation."""

    content_blocks: list[dict]          # normalized via _block_to_api_dict
    content_text: str                   # concatenation of text blocks
    usage: object                       # SDK Usage object or duck-typed equivalent
    tool_uses: list = field(default_factory=list)
    # All tool-use blocks in this response, in declaration order.  The
    # Anthropic & OpenAI APIs both allow the model to emit multiple tool_use
    # blocks per response and require a tool_result for every one of them
    # on the next call.  Plan B's run_turn iterates this list and dispatches
    # each tool sequentially.


class LLMProvider(ABC):
    """Contract that every LLM backend must satisfy.

    Plan B follow-up A: providers now bake the model name into their
    constructor (`__init__(model=..., ...)`) instead of taking it per-call
    via `do_turn(model=...)`.  The ``model`` parameter on ``do_turn()`` is
    kept for backwards compat with ``FallbackLLMProvider`` and any test
    fakes that pass it explicitly, but if empty/None the provider falls
    back to its constructor-baked model.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name (e.g. 'anthropic', 'openai')."""
        ...

    @property
    @abstractmethod
    def model(self) -> str:
        """The model name this provider instance uses."""
        ...

    @abstractmethod
    def do_turn(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        on_text_chunk: Callable[[str], None] | None = None,
        cache_key: str | None = None,
    ) -> LLMTurnResult:
        """Execute one streaming LLM turn and return a normalised result.

        Args:
            model: Model identifier string.  Empty string means "use the
                model baked into the provider's constructor."
            system: System prompt.
            messages: Conversation history in Anthropic wire format.
            tools: List of tool schemas in Anthropic format.  May include
                provider-specific built-in tools (e.g. ``{"type":
                "web_search"}`` for OpenAI Responses API).
            on_text_chunk: Optional callback fired per streamed text delta.
            cache_key: Optional stable per-session key.  OpenAI translates
                this to ``prompt_cache_key`` on the Responses API call,
                which improves cache hit routing when many requests share
                a long prefix.  Anthropic currently has no equivalent in
                the stable Messages API and silently ignores this param
                — its ``cache_control`` breakpoints are positional, not
                key-based.

        Returns:
            LLMTurnResult with all content blocks normalised to plain dicts.
        """
        ...

    @abstractmethod
    def complete(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int = 1024,
        cache_key: str | None = None,
    ) -> tuple[str, object]:
        """Execute one non-streaming text-only completion.

        Plan B follow-up A2: replaces inline ``anthropic.Anthropic(...)``
        and ``openai.OpenAI(...)`` calls scattered across the codebase
        for "small one-off LLM call" use cases (persona generation, TTS
        prep, advisor chat, objectives extraction, title generation,
        OCR transcription).

        Args:
            system: System prompt as a plain string.  Providers may
                wrap it in their own structured-system format internally
                (e.g. Anthropic's cache_control).
            messages: Conversation history in Anthropic wire format.
            max_tokens: Maximum output tokens (named ``max_tokens`` for
                Anthropic compatibility; OpenAI maps it to
                ``max_output_tokens``).
            cache_key: Optional stable per-session key.  See ``do_turn``
                for semantics.

        Returns:
            (text, usage) — the assistant's text response, plus a usage
            object with at least ``input_tokens``, ``output_tokens``, and
            ``cache_read_input_tokens`` fields (duck-typed).
        """
        ...
