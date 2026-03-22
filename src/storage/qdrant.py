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
    ScrollRequest,
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
                "project": item.metadata.get("project"),
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
        session_id: str | None = None,
        project: str | None = None,
    ) -> list[ScoredContextItem]:
        """Similarity search filtered by user_id and optionally memory_layer or session_id."""
        conditions: list[Any] = [
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
            FieldCondition(key="is_deprecated", match=MatchValue(value=False)),
        ]
        if memory_layer:
            conditions.append(
                FieldCondition(key="memory_layer", match=MatchValue(value=memory_layer.value))
            )
        if session_id:
            conditions.append(
                FieldCondition(key="session_id", match=MatchValue(value=session_id))
            )
        if project:
            conditions.append(
                FieldCondition(key="project", match=MatchValue(value=project))
            )

        results = await self._client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            query_filter=Filter(must=conditions),
            limit=top_k,
            score_threshold=score_threshold,
            with_payload=True,
        )
        results = results.points

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

    async def deprecate(self, item_id: str, reason: str | None = None) -> None:
        """Mark a point as deprecated by updating its payload in-place."""
        payload: dict[str, Any] = {"is_deprecated": True}
        if reason:
            payload["deprecated_reason"] = reason
        await self._client.set_payload(
            collection_name=COLLECTION_NAME,
            payload=payload,
            points=[str(uuid.UUID(item_id))],
        )

    async def list_items(
        self,
        user_id: str,
        memory_layer: MemoryLayer | None = None,
        checkpoints_only: bool = False,
        include_deprecated: bool = False,
        project: str | None = None,
        limit: int = 50,
    ) -> list[ContextItem]:
        """Scroll through items without vector search — for listing/browsing."""
        conditions: list[Any] = [
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
        ]
        if not include_deprecated:
            conditions.append(
                FieldCondition(key="is_deprecated", match=MatchValue(value=False))
            )
        if memory_layer:
            conditions.append(
                FieldCondition(key="memory_layer", match=MatchValue(value=memory_layer.value))
            )
        if project:
            conditions.append(
                FieldCondition(key="project", match=MatchValue(value=project))
            )

        results, _ = await self._client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(must=conditions),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )

        items = []
        for r in results:
            p = r.payload or {}
            metadata = p.get("metadata", {})
            # filter checkpoints in Python (no top-level field for checkpoint_name)
            if checkpoints_only and "checkpoint_name" not in metadata:
                continue
            items.append(ContextItem(
                id=str(r.id),
                user_id=p.get("user_id", ""),
                session_id=p.get("session_id"),
                content=p.get("content", ""),
                memory_layer=MemoryLayer(p.get("memory_layer", "L3")),
                importance=p.get("importance", 0.5),
                is_pinned=p.get("is_pinned", False),
                is_deprecated=p.get("is_deprecated", False),
                metadata=metadata,
            ))
        return items

    async def ping(self) -> bool:
        try:
            await self._client.get_collections()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.close()

