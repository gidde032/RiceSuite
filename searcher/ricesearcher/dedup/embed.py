"""Local transcript embedder (D6): sentence-transformers, lazily imported.

Only the model load + encode are lazy and untested (``# pragma: no cover``); the
dedup math is pure and tested in ``annotate.py``. Local and free — no network,
nothing leaves the machine. The ``Embedder`` protocol makes this swappable if the
model stack is awkward to install on a given Python.
"""

from __future__ import annotations

import os

_DEFAULT_MODEL = "all-MiniLM-L6-v2"
_MODEL_ENV = "RICESEARCHER_EMBED_MODEL"


class SentenceTransformerEmbedder:
    """Embed transcript spans with a small local sentence-transformers model."""

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or os.getenv(_MODEL_ENV) or _DEFAULT_MODEL
        self._model = None

    def _load(self):  # pragma: no cover - heavy optional dependency
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover
        if not texts:
            return []
        model = self._load()
        vectors = model.encode(texts, normalize_embeddings=False)
        return [list(map(float, v)) for v in vectors]
