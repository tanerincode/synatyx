from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Protocol

from src.config import settings
from src.core.budget import estimate_tokens
from src.core.usage import usage_add_embedding


class EmbedderProtocol(Protocol):
    async def embed(self, text: str) -> list[float]: ...
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    """Local embedder using sentence-transformers (no API cost)."""

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_name)

    async def embed(self, text: str) -> list[float]:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._model.encode, text)
        usage_add_embedding(estimate_tokens(text))
        return result.tolist()

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, self._model.encode, texts)
        usage_add_embedding(sum(estimate_tokens(t) for t in texts))
        return [r.tolist() for r in results]


class OpenAIEmbedder:
    """Remote embedder using OpenAI text-embedding-ada-002."""

    def __init__(self, api_key: str, model: str) -> None:
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    async def embed(self, text: str) -> list[float]:
        response = await self._client.embeddings.create(input=text, model=self._model)
        self._record_usage(response)
        return response.data[0].embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.embeddings.create(input=texts, model=self._model)
        self._record_usage(response)
        return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]

    @staticmethod
    def _record_usage(response: object) -> None:
        usage = getattr(response, "usage", None)
        if usage is not None:
            usage_add_embedding(usage.total_tokens)


@lru_cache(maxsize=1)
def get_embedder() -> EmbedderProtocol:
    """Return the configured embedder singleton."""
    cfg = settings.embedding
    if cfg.provider == "openai":
        return OpenAIEmbedder(api_key=cfg.openai_api_key, model=cfg.model)
    return SentenceTransformerEmbedder(model_name=cfg.model)

