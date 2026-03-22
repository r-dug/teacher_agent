"""OpenAI image generation provider — DALL-E via the Images API."""

from __future__ import annotations

import base64
import logging

import openai

from .base import GeneratedImage, ImageProvider

log = logging.getLogger(__name__)

# Cost per image in USD for each (model, size, quality) combination.
# Source: OpenAI pricing page (2026-03).
_COST_TABLE: dict[tuple[str, str, str], float] = {
    ("dall-e-2", "256x256",   "standard"): 0.016,
    ("dall-e-2", "512x512",   "standard"): 0.018,
    ("dall-e-2", "1024x1024", "standard"): 0.020,
    ("dall-e-3", "1024x1024", "standard"): 0.040,
    ("dall-e-3", "1024x1024", "hd"):       0.080,
    ("dall-e-3", "1024x1792", "standard"): 0.080,
    ("dall-e-3", "1024x1792", "hd"):       0.120,
    ("dall-e-3", "1792x1024", "standard"): 0.080,
    ("dall-e-3", "1792x1024", "hd"):       0.120,
}


class OpenAIImageProvider(ImageProvider):
    """Generates images via the OpenAI Images API (DALL-E 3)."""

    def __init__(
        self,
        api_key: str,
        model: str = "dall-e-3",
        size: str = "1024x1024",
        quality: str = "standard",
        timeout_seconds: float = 60.0,
        max_retries: int = 1,
    ) -> None:
        self._client = openai.OpenAI(
            api_key=api_key.strip(),
            timeout=timeout_seconds,
            max_retries=max_retries,
        )
        self._model = model
        self._size = size
        self._quality = quality

    @property
    def name(self) -> str:
        return "openai_image"

    def generate(self, prompt: str) -> GeneratedImage:
        log.debug("[OpenAIImageProvider] generating image (model=%s)", self._model)

        kwargs: dict = {
            "model": self._model,
            "prompt": prompt,
            "n": 1,
            "size": self._size,
        }
        if self._model.startswith("dall-e"):
            kwargs["response_format"] = "b64_json"
        if self._model == "dall-e-3":
            kwargs["quality"] = self._quality

        response = self._client.images.generate(**kwargs)

        item = response.data[0]
        image_bytes = base64.b64decode(item.b64_json or "")
        revised_prompt = getattr(item, "revised_prompt", None) or prompt
        cost = _COST_TABLE.get((self._model, self._size, self._quality), 0.0)

        return GeneratedImage(
            image_bytes=image_bytes,
            revised_prompt=revised_prompt,
            estimated_cost_usd=cost,
        )
