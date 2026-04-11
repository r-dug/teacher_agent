"""OpenAI embeddings provider — wraps ``client.embeddings.create``."""

from __future__ import annotations

import logging

import openai

from .embedding_base import EmbeddingProvider

log = logging.getLogger(__name__)


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Calls OpenAI's embeddings endpoint.

    Plan B follow-up A2: replaces the inline
    ``openai.OpenAI(max_retries=1, timeout=10.0)`` construction in
    ``TeacherAgent._init_embeddings``.  Configuration (model, timeout,
    retries) comes from ``EMBEDDING_CHAIN.primary`` in
    ``model_chains.py``.

    Truncates input text to ~2000 characters before embedding to stay
    well under the model's token budget regardless of language.
    """

    _MAX_INPUT_CHARS = 2000

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        timeout_seconds: float = 10.0,
        max_retries: int = 1,
    ) -> None:
        if not (api_key or "").strip():
            raise ValueError("OpenAIEmbeddingProvider requires a non-empty api_key.")
        self._client = openai.OpenAI(
            api_key=api_key.strip(),
            timeout=timeout_seconds,
            max_retries=max_retries,
        )
        self._model = model

    @property
    def name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    def embed(self, text: str) -> list[float]:
        """Embed a single piece of text and return the dense vector."""
        # Truncate to keep inputs well under the model's token budget.
        snippet = text[: self._MAX_INPUT_CHARS]
        resp = self._client.embeddings.create(
            model=self._model,
            input=[snippet],
        )
        return resp.data[0].embedding
