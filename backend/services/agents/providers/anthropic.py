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
            usage=final.usage,
            tool_uses=tool_uses,
        )

    def complete(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int = 1024,
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
        return "\n".join(text_parts).strip(), response.usage
