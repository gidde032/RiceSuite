"""Embedder protocol for cross-source transcript similarity."""

from __future__ import annotations

from typing import Protocol


class Embedder(Protocol):
    """Turns transcript spans into vectors for cosine-similarity comparison."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text, in order."""
        ...
