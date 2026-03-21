from __future__ import annotations

import re
from typing import Any

from src.core.chunker import RecursiveChunker, default_chunker
from src.core.embedder import get_embedder
from src.models.context import ContextItem
from src.models.memory_layer import MemoryLayer
from src.storage.postgres import PostgresStorage
from src.storage.qdrant import QdrantStorage
from src.storage.redis import RedisStorage

# Content longer than this will be chunked before embedding (L2, L3, L4)
CHUNK_THRESHOLD = 600  # characters (~150 tokens)

# Prompt injection patterns to sanitize from stored content
_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions?", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+a", re.IGNORECASE),
    re.compile(r"(system|assistant|user)\s*:\s*", re.IGNORECASE),
    re.compile(r"<\|.*?\|>"),                   # special tokens
    re.compile(r"\[INST\]|\[/INST\]"),          # Llama tokens
    re.compile(r"###\s*(instruction|system|prompt)", re.IGNORECASE),
]


def _sanitize(content: str) -> str:
    """Strip prompt injection patterns from content before storing."""
    for pattern in _INJECTION_PATTERNS:
        content = pattern.sub("[REDACTED]", content)
    return content.strip()


def _validate_user_isolation(item: ContextItem, user_id: str) -> None:
    """Ensure the item belongs to the requesting user."""
    if item.user_id != user_id:
        raise PermissionError(
            f"User isolation violation: item.user_id={item.user_id!r} != user_id={user_id!r}"
        )


class StoreService:
    def __init__(
        self,
        qdrant: QdrantStorage,
        redis: RedisStorage,
        postgres: PostgresStorage,
        chunker: RecursiveChunker | None = None,
    ) -> None:
        self._qdrant = qdrant
        self._redis = redis
        self._postgres = postgres
        self._embedder = get_embedder()
        self._chunker = chunker or default_chunker

    async def store(
        self,
        content: str,
        user_id: str,
        memory_layer: MemoryLayer,
        importance: float = 0.5,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        confidence: float = 1.0,
        is_pinned: bool = False,
    ) -> tuple[str, bool]:
        """
        Store content into the appropriate memory layer.

        For vector layers (L2, L3, L4):
        - Content longer than CHUNK_THRESHOLD is split into chunks first
        - Each chunk is embedded and stored as a separate ContextItem
        - Returns the ID of the first (or only) chunk

        Returns:
            item_id: ID of the first stored item
            embedded: whether content was vectorized and stored in Qdrant
        """
        sanitized = _sanitize(content)
        embedded = False
        base_meta = {**(metadata or {}), "confidence": confidence}

        _validate_user_isolation(
            ContextItem(user_id=user_id, content="x", memory_layer=memory_layer),
            user_id,
        )

        if memory_layer == MemoryLayer.L1:
            item = ContextItem(
                user_id=user_id,
                session_id=session_id,
                content=sanitized,
                memory_layer=memory_layer,
                importance=importance,
                is_pinned=is_pinned,
                metadata=base_meta,
            )
            await self._redis.l1_push(item)
            first_id = item.id

        else:
            # Chunk content if it exceeds threshold (L2, L3, L4)
            if len(sanitized) > CHUNK_THRESHOLD:
                chunks = self._chunker.chunk(sanitized)
            else:
                from src.core.chunker import Chunk
                chunks = [Chunk(text=sanitized, start_pos=0, end_pos=len(sanitized))]

            first_id = ""
            texts = [c.text for c in chunks]
            embeddings = await self._embedder.embed_batch(texts)

            for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                item = ContextItem(
                    user_id=user_id,
                    session_id=session_id,
                    content=chunk.text,
                    memory_layer=memory_layer,
                    importance=importance,
                    is_pinned=is_pinned,
                    embedding=embedding,
                    metadata={
                        **base_meta,
                        "chunk_index": idx,
                        "chunk_total": len(chunks),
                        "start_pos": chunk.start_pos,
                        "end_pos": chunk.end_pos,
                    },
                )
                await self._qdrant.upsert(item)
                if idx == 0:
                    first_id = item.id

            embedded = True

        await self._redis.publish("context_stored", {
            "item_id": first_id,
            "user_id": user_id,
            "memory_layer": memory_layer.value,
            "embedded": embedded,
        })

        await self._postgres.audit(user_id, "context_store", {
            "item_id": first_id,
            "memory_layer": memory_layer.value,
            "importance": importance,
            "embedded": embedded,
        })

        return first_id, embedded

