"""Turning text into vectors for semantic memory.

Every embedder returns exactly EMBEDDING_DIMENSIONS floats, unit-normalised,
because the database column and its index have one fixed width. Vectors from
different models are not comparable even at the same width, so each stored
vector records the model that made it, and search only compares like with like.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Literal

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import EMBEDDING_DIMENSIONS

log = get_logger(__name__)

Purpose = Literal["document", "query"]


def normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def cosine(a: list[float], b: list[float]) -> float:
    """Similarity of two unit vectors, 1.0 being identical in meaning."""
    return sum(x * y for x, y in zip(a, b, strict=True))


class Embedder(ABC):
    # Recorded on every stored vector. Includes the width, since a model asked
    # for a different width produces incomparable vectors.
    name: str

    @abstractmethod
    async def embed(self, texts: list[str], *, purpose: Purpose) -> list[list[float]]: ...


class GoogleEmbedder(Embedder):
    def __init__(self, api_key: str, model: str) -> None:
        from google import genai

        self.model = model
        self.name = f"google/{model}@{EMBEDDING_DIMENSIONS}"
        self._client = genai.Client(api_key=api_key)

    async def embed(self, texts: list[str], *, purpose: Purpose) -> list[list[float]]:
        from google.genai import types

        response = await self._client.aio.models.embed_content(
            model=self.model,
            contents=texts,
            config=types.EmbedContentConfig(
                # Asymmetric retrieval: documents and queries embed differently.
                task_type="RETRIEVAL_DOCUMENT" if purpose == "document" else "RETRIEVAL_QUERY",
                output_dimensionality=EMBEDDING_DIMENSIONS,
            ),
        )
        # Truncated Gemini embeddings are not unit length; cosine needs them to be.
        return [normalise(list(e.values or [])) for e in response.embeddings or []]


class OpenAIEmbedder(Embedder):
    def __init__(self, api_key: str, model: str) -> None:
        from openai import AsyncOpenAI

        self.model = model
        self.name = f"openai/{model}@{EMBEDDING_DIMENSIONS}"
        self._client = AsyncOpenAI(api_key=api_key)

    async def embed(self, texts: list[str], *, purpose: Purpose) -> list[list[float]]:
        response = await self._client.embeddings.create(
            model=self.model, input=texts, dimensions=EMBEDDING_DIMENSIONS
        )
        return [normalise(list(item.embedding)) for item in response.data]


def get_embedder() -> Embedder | None:
    """The configured embedder, or None — memory then uses keyword search."""
    choice = settings.embedding_provider
    if choice == "none":
        return None
    if choice in ("auto", "google") and settings.google_api_key:
        return GoogleEmbedder(settings.google_api_key, settings.google_embedding_model)
    if choice in ("auto", "openai") and settings.openai_api_key:
        return OpenAIEmbedder(settings.openai_api_key, settings.openai_embedding_model)
    if choice != "auto":
        log.warning("embedder_unconfigured", provider=choice)
    return None
