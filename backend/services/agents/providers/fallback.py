"""FallbackLLMProvider — chain of providers tried in order on failure."""

from __future__ import annotations

import logging
from collections.abc import Callable

from .base import LLMProvider, LLMTurnResult

log = logging.getLogger(__name__)


class FallbackLLMProvider(LLMProvider):
    """Wraps an ordered list of (provider, model) pairs.

    On ``do_turn()`` and ``complete()``, tries each provider with its
    configured model in sequence.  Advances to the next on any exception
    (e.g. rate limit, timeout, network error).  Raises ``RuntimeError``
    only after all providers are exhausted.

    Plan B follow-up A: each entry is now ``(provider, model)`` where
    ``model`` is baked into the provider's constructor too.  We pass it
    to ``do_turn(model=...)`` for backwards compat with the existing
    interface, but providers internally use their constructor model when
    the per-call model is empty.
    """

    def __init__(self, providers: list[tuple[LLMProvider, str]]) -> None:
        if not providers:
            raise ValueError("FallbackLLMProvider requires at least one provider.")
        self._providers = list(providers)

    @property
    def name(self) -> str:
        return "/".join(p.name for p, _ in self._providers)

    @property
    def model(self) -> str:
        """The primary provider's model.  Fallbacks may use different models."""
        return self._providers[0][1]

    def do_turn(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        on_text_chunk: Callable[[str], None] | None = None,
    ) -> LLMTurnResult:
        errors: list[str] = []
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
                errors.append(f"[{provider.name}/{pmodel}] {exc}")
                last_exc = exc

        chain_summary = " → ".join(errors)
        raise RuntimeError(
            f"All LLM providers exhausted. Chain: {chain_summary}"
        ) from last_exc

    def complete(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int = 1024,
    ) -> tuple[str, object]:
        """Try each provider's ``complete()`` in sequence."""
        errors: list[str] = []
        last_exc: Exception | None = None
        for idx, (provider, pmodel) in enumerate(self._providers):
            if idx > 0:
                log.warning(
                    "[FallbackLLMProvider] complete: switching to %s (model=%s) after prior failure",
                    provider.name,
                    pmodel,
                )
            try:
                return provider.complete(system, messages, max_tokens=max_tokens)
            except Exception as exc:
                log.warning(
                    "[FallbackLLMProvider] complete: %s (model=%s) failed: %s",
                    provider.name,
                    pmodel,
                    exc,
                )
                errors.append(f"[{provider.name}/{pmodel}] {exc}")
                last_exc = exc

        chain_summary = " → ".join(errors)
        raise RuntimeError(
            f"All LLM providers exhausted (complete). Chain: {chain_summary}"
        ) from last_exc
