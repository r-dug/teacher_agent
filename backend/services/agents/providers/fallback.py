"""FallbackLLMProvider — chain of providers tried in order on failure."""

from __future__ import annotations

import logging
from collections.abc import Callable

from .base import LLMProvider, LLMTurnResult

log = logging.getLogger(__name__)


class FallbackLLMProvider(LLMProvider):
    """Wraps an ordered list of (provider, model) pairs.

    On ``do_turn()``, tries each provider with its configured model in sequence.
    Advances to the next on any exception (e.g. rate limit, timeout, network error).
    Raises RuntimeError only after all providers are exhausted.

    The ``model`` parameter passed to ``do_turn()`` is intentionally ignored;
    each entry in the chain uses the model it was constructed with.
    """

    def __init__(self, providers: list[tuple[LLMProvider, str]]) -> None:
        if not providers:
            raise ValueError("FallbackLLMProvider requires at least one provider.")
        self._providers = list(providers)

    @property
    def name(self) -> str:
        return "/".join(p.name for p, _ in self._providers)

    def do_turn(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        on_text_chunk: Callable[[str], None] | None = None,
    ) -> LLMTurnResult:
        last_exc: Exception | None = None
        for idx, (provider, pmodel) in enumerate(self._providers):
            if idx > 0:
                log.warning(
                    "[FallbackLLMProvider] switching to %s (model=%s) after prior failure",
                    provider.name,
                    pmodel,
                )
            try:
                return provider.do_turn(pmodel, system, messages, tools, on_text_chunk)
            except Exception as exc:
                log.warning(
                    "[FallbackLLMProvider] %s (model=%s) failed: %s",
                    provider.name,
                    pmodel,
                    exc,
                )
                last_exc = exc

        raise RuntimeError(
            f"All LLM providers exhausted. Last error: {last_exc}"
        ) from last_exc
