from __future__ import annotations

import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from src.models.context import ContextItem, ScoredContextItem
from src.models.memory_layer import MemoryLayer

COLLECTION_NAME = "synatyx_context"
VECTOR_SIZE = 1536  # OpenAI ada-002 / sentence-transformers default


class QdrantStorage:
    def __init__(self, host: str = "localhost", port: int = 6333) -> None:
        self._client = AsyncQdrantClient(host=host, port=port)

    async def init_collection(self) -> None:
        """Create the collection if it doesn't exist."""
        collections = await self._client.get_collections()
        names = [c.name for c in collections.collections]
        if COLLECTION_NAME not in names:
            await self._client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )

    async def upsert(self, item: ContextItem) -> str:
        """Insert or update a ContextItem in the vector store."""
        if item.embedding is None:
            raise ValueError(f"ContextItem {item.id} has no embedding — cannot upsert to Qdrant")

        point = PointStruct(
            id=str(uuid.UUID(item.id)),
            vector=item.embedding,
            payload={
                "user_id": item.user_id,
                "session_id": item.session_id,
                "content": item.content,
                "memory_layer": item.memory_layer.value,
                "importance": item.importance,
                "is_pinned": item.is_pinned,
                "is_deprecated": item.is_deprecated,
                "metadata": item.metadata,
                "created_at": item.created_at.isoformat(),
            },
        )
        await self._client.upsert(collection_name=COLLECTION_NAME, points=[point])
        return item.id

    async def search(
        self,
        query_vector: list[float],
        user_id: str,
        top_k: int = 10,
        memory_layer: MemoryLayer | None = None,
        score_threshold: float = 0.0,
    ) -> list[ScoredContextItem]:
        """Similarity search filtered by user_id and optionally memory_layer."""
        conditions: list[Any] = [
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
            FieldCondition(key="is_deprecated", match=MatchValue(value=False)),
        ]
        if memory_layer:
            conditions.append(
                FieldCondition(key="memory_layer", match=MatchValue(value=memory_layer.value))
            )

        results = await self._client.search(
            collection_name=COLLECTION_NAME,
            query_vector=query_vector,
            query_filter=Filter(must=conditions),
            limit=top_k,
            score_threshold=score_threshold,
            with_payload=True,
        )

        items = []
        for r in results:
            p = r.payload or {}
            item = ScoredContextItem(
                id=str(r.id),
                user_id=p.get("user_id", ""),
                session_id=p.get("session_id"),
                content=p.get("content", ""),
                memory_layer=MemoryLayer(p.get("memory_layer", "L3")),
                importance=p.get("importance", 0.5),
                is_pinned=p.get("is_pinned", False),
                is_deprecated=p.get("is_deprecated", False),
                metadata=p.get("metadata", {}),
                score=r.score,
                semantic_score=r.score,
            )
            items.append(item)
        return items

    async def delete(self, item_id: str) -> None:
        """Delete a single point by ID."""
        await self._client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=[str(uuid.UUID(item_id))],
        )

    async def delete_by_user(self, user_id: str) -> None:
        """Delete all points belonging to a user."""
        await self._client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            ),
        )

    async def close(self) -> None:
        await self._client.close()

