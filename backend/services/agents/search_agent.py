"""SearchAgent — general-purpose web search agent."""

from __future__ import annotations

import logging
from collections.abc import Callable

from .agent import Agent
from .config import DEFAULT_LLM_MODEL
from .prompts.search import SEARCH_GUARDRAIL_SYSTEM

log = logging.getLogger(__name__)


class SearchAgent(Agent):
    """
    General-purpose web search agent.

    Wraps Anthropic's web search beta to retrieve factual summaries from
    trustworthy sources.  Can be used standalone or as a dependency of other
    agents (e.g. LessonPlannerAgent).
    """

    def __init__(
        self,
        model: str = DEFAULT_LLM_MODEL,
        on_token_usage: Callable[[str, str, object], None] | None = None,
    ) -> None:
        super().__init__(model)
        self._on_token_usage = on_token_usage

    def search(self, query: str) -> str:
        """
        Search the web for factual information about *query*.

        Returns a concise factual summary string, or a best-effort error
        message if the search fails so callers can continue gracefully.
        """
        import anthropic

        client = anthropic.Anthropic(max_retries=3)
        try:
            response = client.beta.messages.create(
                model=self._model,
                max_tokens=1024,
                system=SEARCH_GUARDRAIL_SYSTEM,
                betas=["web-search-2025-03-05"],
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
                messages=[{
                    "role": "user",
                    "content": (
                        "Find factual information about the following to help plan a lesson. "
                        f"Use only the most trustworthy sources available.\n\nQuery: {query}"
                    ),
                }],
            )
            if self._on_token_usage:
                self._on_token_usage("web_search", self._model, response.usage)
            for block in response.content:
                if getattr(block, "type", None) == "text" and block.text.strip():
                    return block.text.strip()
            return "No relevant information found from trustworthy sources."
        except Exception as exc:
            log.warning("SearchAgent.search failed: %s", exc)
            return f"Search unavailable ({exc}). Continue with document content only."
