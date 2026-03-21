from __future__ import annotations

import re
from typing import Any

from src.core.embedder import get_embedder
from src.models.context import ContextItem
from src.models.memory_layer import MemoryLayer
from src.storage.postgres import PostgresStorage
from src.storage.qdrant import QdrantStorage
from src.storage.redis import RedisStorage

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
    ) -> None:
        self._qdrant = qdrant
        self._redis = redis
        self._postgres = postgres
        self._embedder = get_embedder()

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
        Store a piece of content into the appropriate memory layer.

        Returns:
            item_id: the ID of the stored item
            embedded: whether the item was vectorized and stored in Qdrant
        """
        sanitized = _sanitize(content)

        item = ContextItem(
            user_id=user_id,
            session_id=session_id,
            content=sanitized,
            memory_layer=memory_layer,
            importance=importance,
            is_pinned=is_pinned,
            metadata={
                **(metadata or {}),
                "confidence": confidence,
            },
        )

        _validate_user_isolation(item, user_id)

        embedded = False

        if memory_layer == MemoryLayer.L1:
            await self._redis.l1_push(item)

        elif memory_layer == MemoryLayer.L3:
            item.embedding = await self._embedder.embed(sanitized)
            await self._qdrant.upsert(item)
            embedded = True

        elif memory_layer in (MemoryLayer.L2, MemoryLayer.L4):
            # L2 session summaries and L4 user preferences go to Qdrant for
            # semantic retrieval, and are also tracked via postgres/redis budget
            item.embedding = await self._embedder.embed(sanitized)
            await self._qdrant.upsert(item)
            embedded = True

        # Publish store event for GraphQL subscriptions
        await self._redis.publish("context_stored", {
            "item_id": item.id,
            "user_id": user_id,
            "memory_layer": memory_layer.value,
            "embedded": embedded,
        })

        await self._postgres.audit(user_id, "context_store", {
            "item_id": item.id,
            "memory_layer": memory_layer.value,
            "importance": importance,
            "embedded": embedded,
        })

        return item.id, embedded

