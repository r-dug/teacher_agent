"""SearchAgent — general-purpose web search agent.

Plan B follow-up A2: rewritten to use OpenAI's Responses API built-in
``web_search`` tool.  Previously used Anthropic's beta web_search tool
(``betas=["web-search-2025-03-05"]``) which required Anthropic API
credits — and the deployed environment has zero credit balance.

The OpenAI Responses API ``web_search`` tool is server-managed: OpenAI
runs the search and returns synthesized text + citations.  No
client-side dispatch loop, no tool_use blocks to process — it just
returns ``output_text`` like a normal completion.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from .agent import Agent
from .clients import openai_client_for
from .model_chains import SEARCH_CHAIN
from .prompts.search import SEARCH_GUARDRAIL_SYSTEM

log = logging.getLogger(__name__)


class SearchAgent(Agent):
    """
    General-purpose web search agent.

    Wraps OpenAI's Responses API ``web_search`` built-in tool to retrieve
    factual summaries from public sources.  Can be used standalone or as
    a dependency of other agents (e.g. LessonPlannerAgent).

    Plan B follow-up A2: model and config come from
    ``SEARCH_CHAIN.primary`` in ``model_chains.py``.  Use
    ``openai_client_for("search")`` to construct the SDK client with the
    centralized timeout / retries config.
    """

    def __init__(
        self,
        on_token_usage: Callable[[str, str, object], None] | None = None,
    ) -> None:
        super().__init__(SEARCH_CHAIN.primary.name)
        self._on_token_usage = on_token_usage

    def search(self, query: str) -> str:
        """
        Search the web for factual information about *query*.

        Returns a concise factual summary string, or a best-effort error
        message if the search fails so callers can continue gracefully.
        """
        try:
            client = openai_client_for("search")
        except ValueError as exc:
            # No API key configured — degrade gracefully.
            log.warning("SearchAgent.search not configured: %s", exc)
            return f"Search unavailable ({exc}). Continue with document content only."

        try:
            response = client.responses.create(
                model=SEARCH_CHAIN.primary.name,
                instructions=SEARCH_GUARDRAIL_SYSTEM,
                input=(
                    "Find factual information about the following to help plan a lesson. "
                    "Use only the most trustworthy sources available.\n\n"
                    f"Query: {query}"
                ),
                tools=[{"type": "web_search"}],
                stream=False,
            )
            if self._on_token_usage:
                self._on_token_usage("web_search", self._model, getattr(response, "usage", None))
            text = (getattr(response, "output_text", None) or "").strip()
            return text or "No relevant information found from trustworthy sources."
        except Exception as exc:
            log.warning("SearchAgent.search failed: %s", exc)
            return f"Search unavailable ({exc}). Continue with document content only."
