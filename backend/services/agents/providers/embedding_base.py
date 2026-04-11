"""Abstract base for embedding providers.

Plan B follow-up A2: separate from ``LLMProvider`` because embedding APIs
have a fundamentally different shape (no streaming, no tools, just
``embed(text) -> vector``).  Used by the RAG memory strategy in
``backend/services/agents/memory_strategies.py``.

Currently only one implementation (``OpenAIEmbeddingProvider``); the
abstraction exists so a future Cohere / Voyage / local-embedding
implementation can drop in without changing call sites.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """Contract that every embedding backend must satisfy."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name (e.g. 'openai')."""
        ...

    @property
    @abstractmethod
    def model(self) -> str:
        """Model identifier used by this provider instance."""
        ...

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Embed a single piece of text.

        Args:
            text: Input text to embed.  Implementations may truncate to
                  fit the model's context window.

        Returns:
            A dense vector of floats (length depends on the model).

        Raises:
            Exception: any provider error (network, auth, rate limit).
            Callers are expected to catch broadly because the embedding
            cache falls back to recency-based memory if embedding fails.
        """
        ...
