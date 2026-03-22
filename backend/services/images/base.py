"""Abstract base for image generation providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class GeneratedImage:
    """Result of a single image generation call."""

    image_bytes: bytes         # raw PNG/JPEG bytes
    revised_prompt: str        # prompt as actually used by the provider
    estimated_cost_usd: float  # 0.0 when unknown


class ImageProvider(ABC):
    """Minimal interface that all image generation backends must implement."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def generate(self, prompt: str) -> GeneratedImage:
        """Generate an image from *prompt*.  Synchronous; call from a thread."""
        ...
