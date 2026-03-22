"""Image generation service — factory and public exports."""

from __future__ import annotations

import logging

from .base import GeneratedImage, ImageProvider

log = logging.getLogger(__name__)

__all__ = ["GeneratedImage", "ImageProvider", "build_image_provider"]


def build_image_provider(
    enable: bool,
    provider: str,
    model: str,
    size: str,
    quality: str,
    timeout_seconds: float,
    max_retries: int,
    openai_api_key: str | None = None,
) -> ImageProvider | None:
    """Return a configured ImageProvider or None if prerequisites are missing.

    Returns None (with a log warning) instead of raising so callers can
    gracefully disable image generation rather than crash on startup.
    """
    if not enable:
        return None

    provider = provider.strip().lower()

    if provider == "openai":
        key = (openai_api_key or "").strip()
        if not key:
            log.warning(
                "[build_image_provider] IMAGE_GEN_ENABLE=true but OPENAI_API_KEY is not set "
                "— image generation disabled."
            )
            return None
        from .openai import OpenAIImageProvider
        return OpenAIImageProvider(
            api_key=key,
            model=model,
            size=size,
            quality=quality,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )

    log.warning(
        "[build_image_provider] Unknown provider %r — image generation disabled.", provider
    )
    return None
