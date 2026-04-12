"""Anthropic LLM provider — streaming via client.messages.stream()."""

from __future__ import annotations

import logging
from collections.abc import Callable
from types import SimpleNamespace

import anthropic

from ..message_utils import _block_to_api_dict
from .base import LLMProvider, LLMTurnResult

log = logging.getLogger(__name__)


class AnthropicLLMProvider(LLMProvider):
    """Streams responses from the Anthropic Messages API.

    Plan B follow-up A: model and api_key are now baked into the
    constructor (was: model passed per-call via ``do_turn(model=...)``,
    api_key picked up from env var by the SDK).  ``do_turn(model=...)``
    still accepts the param for backwards compat with FallbackLLMProvider
    and test fakes; if empty, it falls back to ``self._model``.
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        max_retries: int = 6,
    ) -> None:
        if not (model or "").strip():
            raise ValueError("AnthropicLLMProvider requires a non-empty model.")
        if not (api_key or "").strip():
            raise ValueError("AnthropicLLMProvider requires a non-empty api_key.")
        self._client = anthropic.Anthropic(api_key=api_key.strip(), max_retries=max_retries)
        self._model = model

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def model(self) -> str:
        return self._model

    def do_turn(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        on_text_chunk: Callable[[str], None] | None = None,
        cache_key: str | None = None,  # noqa: ARG002 — no Anthropic equivalent; positional cache_control is used instead
    ) -> LLMTurnResult:
        effective_model = (model or "").strip() or self._model
        stream_kwargs: dict = dict(
            model=effective_model,
            max_tokens=2048,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
        )
        if tools:
            # Cache the last tool definition — system + tools form a stable
            # prefix that doesn't change between turns.
            cached_tools = list(tools)
            if cached_tools:
                cached_tools[-1] = {**cached_tools[-1], "cache_control": {"type": "ephemeral"}}
            stream_kwargs["tools"] = cached_tools

        log.info(
            "AnthropicLLMProvider.do_turn: opening stream (model=%s, messages=%d, tools=%d)",
            effective_model, len(messages), len(tools),
        )

        full_text = ""
        with self._client.messages.stream(**stream_kwargs) as stream:
            for chunk in stream.text_stream:
                full_text += chunk
                if on_text_chunk:
                    on_text_chunk(chunk)
            final = stream.get_final_message()

        content_blocks = [_block_to_api_dict(b) for b in final.content]

        tool_uses: list = [
            block for block in final.content
            if getattr(block, "type", None) == "tool_use"
        ]

        return LLMTurnResult(
            content_blocks=content_blocks,
            content_text=full_text,
            usage=_normalize_anthropic_usage(final.usage),
            tool_uses=tool_uses,
        )

    def complete(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int = 1024,
        cache_key: str | None = None,  # noqa: ARG002 — no Anthropic equivalent
    ) -> tuple[str, object]:
        """Plan B follow-up A2: non-streaming text completion.

        Used by the simple text-completion call sites that previously
        constructed ``anthropic.Anthropic(max_retries=6)`` inline.
        """
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
        )
        # Concatenate any text content blocks (Anthropic returns a list).
        text_parts: list[str] = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
        return "\n".join(text_parts).strip(), _normalize_anthropic_usage(response.usage)


def _normalize_anthropic_usage(raw_usage) -> SimpleNamespace:
    """Wrap an Anthropic SDK Usage object so ``input_tokens`` means TOTAL.

    Anthropic's raw ``usage.input_tokens`` excludes the cached portions
    (``cache_read_input_tokens`` and ``cache_creation_input_tokens`` are
    reported as separate buckets).  OpenAI's Responses API reports
    ``input_tokens`` as the total including cached tokens — so the two
    have different semantics out of the box.

    To give downstream code (the teacher-agent log line, the session
    cache-savings accumulator, ``usage_tracker._api_cost``) a single
    consistent shape, we normalize both providers to the convention
    ``input_tokens = TOTAL input (including cached reads + cache
    creates)``.  OpenAI already uses this convention; Anthropic needs
    the addition done here.

    Cache Plan C2 discovered the inconsistency once prefix caching
    actually started landing hits — the old ``_api_cost`` formula
    ``inp*rate + cr*cache_rate`` double-counted the cached portion on
    OpenAI (since ``inp`` was the total and ``cr`` was a subset of it).
    Normalizing + updating the cost formula fixes that.
    """
    raw_input = getattr(raw_usage, "input_tokens", 0) or 0
    cache_read = getattr(raw_usage, "cache_read_input_tokens", 0) or 0
    cache_create = getattr(raw_usage, "cache_creation_input_tokens", 0) or 0
    return SimpleNamespace(
        input_tokens=raw_input + cache_read + cache_create,
        output_tokens=getattr(raw_usage, "output_tokens", 0) or 0,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_create,
    )
