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

DEFAULT_COLLECTION = "ctx_default"
VECTOR_SIZE = 1536  # OpenAI ada-002 / sentence-transformers default


class QdrantStorage:
    def __init__(
        self,
        host: str = "localhost",
        port: int = 6333,
        collection_name: str = DEFAULT_COLLECTION,
    ) -> None:
        self._host = host
        self._port = port
        self._collection_name = collection_name
        self._client = AsyncQdrantClient(host=host, port=port)

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def collection_name(self) -> str:
        return self._collection_name

    @property
    def project_slug(self) -> str | None:
        """Project slug implied by the collection name (ctx_<slug>).

        None for the shared/default collections (ctx_users, ctx_default) where
        a project scope is meaningless — their points must not match any
        project filter.
        """
        prefix = "ctx_"
        if not self._collection_name.startswith(prefix):
            return None
        slug = self._collection_name[len(prefix):]
        return None if slug in ("users", "default") else slug

    def scoped(self, collection_name: str) -> QdrantStorage:
        """Return a view of this storage bound to another collection.

        Shares the underlying client — do not call close() on the returned
        instance. Used by GC to iterate collections without mutating the
        shared storage's collection scope.
        """
        clone = object.__new__(QdrantStorage)
        clone._host = self._host
        clone._port = self._port
        clone._collection_name = collection_name
        clone._client = self._client
        return clone

    @property
    def client(self) -> AsyncQdrantClient:
        return self._client

    async def init_collection(self) -> None:
        """Create the collection if it doesn't exist, with keyword indexes on
        the fields every hot filter uses."""
        collections = await self._client.get_collections()
        names = [c.name for c in collections.collections]
        if self._collection_name not in names:
            await self._client.create_collection(
                collection_name=self._collection_name,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
        await self.ensure_payload_indexes({
            "user_id": "keyword",
            "memory_layer": "keyword",
            "is_deprecated": "bool",
            "project": "keyword",
            "session_id": "keyword",
            "type": "keyword",
        })

    async def ensure_payload_indexes(self, schema: dict[str, Any]) -> None:
        """Idempotently create payload indexes. Values are either a Qdrant
        field-schema string ("keyword", "integer", "bool") or a params object
        (e.g. TextIndexParams). Failures are logged and swallowed — an index
        is an optimization, never a reason to refuse startup."""
        import logging
        logger = logging.getLogger(__name__)
        for field, field_schema in schema.items():
            try:
                await self._client.create_payload_index(
                    collection_name=self._collection_name,
                    field_name=field,
                    field_schema=field_schema,
                )
            except Exception as exc:
                # "already exists" lands here too — that's the common case
                logger.debug(
                    "Payload index %s.%s not created: %s",
                    self._collection_name, field, exc,
                )

    # ------------------------------------------------------------------
    # Point-level helpers for the code/doc index (deterministic ids,
    # condition-based sweeps, full-text lookups)
    # ------------------------------------------------------------------

    async def upsert_points(self, points: list[PointStruct]) -> None:
        if points:
            await self._client.upsert(collection_name=self._collection_name, points=points)

    async def retrieve_points(
        self, ids: list[str], with_payload: bool = True, with_vectors: bool = False
    ) -> list[Any]:
        if not ids:
            return []
        return await self._client.retrieve(
            collection_name=self._collection_name,
            ids=ids,
            with_payload=with_payload,
            with_vectors=with_vectors,
        )

    async def set_payload(self, payload: dict[str, Any], ids: list[str]) -> None:
        """Merge payload keys into existing points by id (no vector change)."""
        if ids:
            await self._client.set_payload(
                collection_name=self._collection_name,
                payload=payload,
                points=ids,
            )

    async def delete_by_conditions(self, conditions: list[Any]) -> None:
        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=Filter(must=conditions),
        )

    async def scroll_by_conditions(
        self,
        conditions: list[Any],
        limit: int = 1000,
        offset: Any = None,
        with_payload: bool = True,
    ) -> tuple[list[Any], Any]:
        records, next_offset = await self._client.scroll(
            collection_name=self._collection_name,
            scroll_filter=Filter(must=conditions),
            limit=limit,
            offset=offset,
            with_payload=with_payload,
            with_vectors=False,
        )
        return records, next_offset

    async def text_search(self, conditions: list[Any], limit: int = 20) -> list[Any]:
        """Keyword lookup via a scroll with MatchText/MatchValue conditions —
        finds exact identifiers dense search can miss."""
        records, _ = await self.scroll_by_conditions(conditions, limit=limit)
        return records

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
                # Every point carries its project scope so project filters can
                # match; the collection itself is per-project, so the slug is
                # authoritative when the caller didn't set metadata.project.
                "project": item.metadata.get("project") or self.project_slug,
                "content": item.content,
                "memory_layer": item.memory_layer.value,
                "importance": item.importance,
                "is_pinned": item.is_pinned,
                "is_deprecated": item.is_deprecated,
                "metadata": item.metadata,
                "created_at": item.created_at.isoformat(),
            },
        )
        await self._client.upsert(collection_name=self._collection_name, points=[point])
        return item.id

    async def skill_upsert(
        self,
        skill_id: str,
        name: str,
        slug: str,
        vector: list[float],
        user_id: str,
        project: str | None = None,
    ) -> None:
        """Store a skill description embedding into L3 with type='skill' payload."""
        point = PointStruct(
            id=str(uuid.UUID(skill_id)),
            vector=vector,
            payload={
                "user_id": user_id,
                "session_id": None,
                "project": project or self.project_slug,
                "content": name,
                "memory_layer": MemoryLayer.L3.value,
                "importance": 0.8,
                "is_pinned": False,
                "is_deprecated": False,
                "type": "skill",
                "metadata": {
                    "skill_id": skill_id,
                    "name": name,
                    "slug": slug,
                    "project": project,
                    "type": "skill",
                },
            },
        )
        await self._client.upsert(collection_name=self._collection_name, points=[point])

    async def search(
        self,
        query_vector: list[float],
        user_id: str,
        top_k: int = 10,
        memory_layer: MemoryLayer | None = None,
        score_threshold: float = 0.0,
        session_id: str | None = None,
        project: str | None = None,
        type_filter: str | None = None,
        pinned_only: bool = False,
        metadata_filters: dict[str, Any] | None = None,
    ) -> list[ScoredContextItem]:
        """Similarity search filtered by user_id and optionally memory_layer or session_id.

        `metadata_filters` match the point's nested metadata object —
        {"locale": "en"} becomes a condition on `metadata.locale`. They are
        pushed into the Qdrant filter rather than applied to the results,
        because filtering afterwards silently shrinks the answer: a top-k of 8
        that returns 8 hits and then drops 6 for the wrong locale leaves 2.
        """
        conditions: list[Any] = [
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
            FieldCondition(key="is_deprecated", match=MatchValue(value=False)),
        ]
        if pinned_only:
            conditions.append(
                FieldCondition(key="is_pinned", match=MatchValue(value=True))
            )
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
        if type_filter:
            conditions.append(
                FieldCondition(key="type", match=MatchValue(value=type_filter))
            )
        for key, value in (metadata_filters or {}).items():
            conditions.append(
                FieldCondition(key=f"metadata.{key}", match=MatchValue(value=value))
            )

        results = await self._client.query_points(
            collection_name=self._collection_name,
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
            kwargs: dict[str, Any] = {
                "id": str(r.id),
                "user_id": p.get("user_id", ""),
                "session_id": p.get("session_id"),
                "content": p.get("content", ""),
                "memory_layer": MemoryLayer(p.get("memory_layer", "L3")),
                "importance": p.get("importance", 0.5),
                "is_pinned": p.get("is_pinned", False),
                "is_deprecated": p.get("is_deprecated", False),
                "metadata": p.get("metadata", {}),
                "score": r.score,
                "semantic_score": r.score,
            }
            # Restore the stored timestamp — defaulting to "now" would make
            # every hit look brand new to the recency signal.
            created_raw = p.get("created_at")
            if created_raw:
                from datetime import datetime
                kwargs["created_at"] = datetime.fromisoformat(created_raw)
            items.append(ScoredContextItem(**kwargs))
        return items

    @staticmethod
    def _payload_to_item(point_id: Any, payload: dict[str, Any]) -> ContextItem:
        """Rebuild a ContextItem from a Qdrant point payload, preserving created_at."""
        from datetime import datetime
        kwargs: dict[str, Any] = {
            "id": str(point_id),
            "user_id": payload.get("user_id", ""),
            "session_id": payload.get("session_id"),
            "content": payload.get("content", ""),
            "memory_layer": MemoryLayer(payload.get("memory_layer", "L3")),
            "importance": payload.get("importance", 0.5),
            "is_pinned": payload.get("is_pinned", False),
            "is_deprecated": payload.get("is_deprecated", False),
            "metadata": payload.get("metadata", {}),
        }
        created_raw = payload.get("created_at")
        if created_raw:
            kwargs["created_at"] = datetime.fromisoformat(created_raw)
        return ContextItem(**kwargs)

    async def get_by_id(self, item_id: str) -> ContextItem | None:
        """Fetch a single point by ID — no scan, no user filter (caller must
        enforce ownership on the returned item)."""
        records = await self._client.retrieve(
            collection_name=self._collection_name,
            ids=[str(uuid.UUID(item_id))],
            with_payload=True,
            with_vectors=False,
        )
        if not records:
            return None
        r = records[0]
        return self._payload_to_item(r.id, r.payload or {})

    async def get_vector(self, item_id: str) -> list[float] | None:
        """Fetch a point's stored embedding by ID (None if the point is missing)."""
        records = await self._client.retrieve(
            collection_name=self._collection_name,
            ids=[str(uuid.UUID(item_id))],
            with_payload=False,
            with_vectors=True,
        )
        if not records or records[0].vector is None:
            return None
        vector = records[0].vector
        return list(vector) if isinstance(vector, list) else None

    async def delete(self, item_id: str) -> None:
        """Delete a single point by ID."""
        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=[str(uuid.UUID(item_id))],
        )

    async def delete_by_user(self, user_id: str) -> None:
        """Delete all points belonging to a user."""
        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
            ),
        )

    async def touch(self, item_ids: list[str]) -> None:
        """Update last_accessed_at for retrieved items — used by access tracking."""
        from datetime import datetime, timezone
        if not item_ids:
            return
        now = datetime.now(timezone.utc).isoformat()
        try:
            await self._client.set_payload(
                collection_name=self._collection_name,
                payload={"last_accessed_at": now},
                points=[str(uuid.UUID(item_id)) for item_id in item_ids],
            )
        except Exception:
            pass  # never block retrieval on touch failures

    async def deprecate(self, item_id: str, reason: str | None = None) -> None:
        """Mark a point as deprecated by updating its payload in-place."""
        from datetime import datetime, timezone
        payload: dict[str, Any] = {
            "is_deprecated": True,
            "deprecated_at": datetime.now(timezone.utc).isoformat(),
        }
        if reason:
            payload["deprecated_reason"] = reason
        await self._client.set_payload(
            collection_name=self._collection_name,
            payload=payload,
            points=[str(uuid.UUID(item_id))],
        )

    async def scan_all_items(
        self,
        memory_layer: MemoryLayer | None = None,
        include_deprecated: bool = False,
        limit: int = 1000,
        offset: str | None = None,
        with_vectors: bool = False,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Scroll ALL items in the collection without user_id filter — for GC/consolidation use only.

        Returns (items_payload_list, next_offset) for pagination. With
        with_vectors=True each payload dict also carries its embedding under
        "_vector".
        """
        conditions: list[Any] = []
        if not include_deprecated:
            conditions.append(
                FieldCondition(key="is_deprecated", match=MatchValue(value=False))
            )
        if memory_layer:
            conditions.append(
                FieldCondition(key="memory_layer", match=MatchValue(value=memory_layer.value))
            )

        scroll_filter = Filter(must=conditions) if conditions else None

        results, next_page = await self._client.scroll(
            collection_name=self._collection_name,
            scroll_filter=scroll_filter,
            limit=limit,
            offset=offset,
            with_payload=True,
            with_vectors=with_vectors,
        )

        items = []
        for r in results:
            p = r.payload or {}
            p["_id"] = str(r.id)
            if with_vectors and isinstance(r.vector, list):
                p["_vector"] = r.vector
            items.append(p)

        next_offset = str(next_page) if next_page else None
        return items, next_offset

    async def hard_delete(self, item_id: str) -> None:
        """Permanently remove a point from the collection."""
        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=[str(uuid.UUID(item_id))],
        )

    async def get_all_collections(self) -> list[str]:
        """Return all collection names — used by GC to iterate over all projects."""
        collections = await self._client.get_collections()
        return [c.name for c in collections.collections]

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
            collection_name=self._collection_name,
            scroll_filter=Filter(must=conditions),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )

        items = []
        for r in results:
            p = r.payload or {}
            # filter checkpoints in Python (no top-level field for checkpoint_name)
            if checkpoints_only and "checkpoint_name" not in p.get("metadata", {}):
                continue
            items.append(self._payload_to_item(r.id, p))
        return items

    async def count_items(
        self,
        user_id: str,
        memory_layer: MemoryLayer | None = None,
        project: str | None = None,
        session_id: str | None = None,
        include_deprecated: bool = False,
    ) -> int:
        """Exact count of a user's items — used for brief stats and retrieval diagnostics."""
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
        if session_id:
            conditions.append(
                FieldCondition(key="session_id", match=MatchValue(value=session_id))
            )

        result = await self._client.count(
            collection_name=self._collection_name,
            count_filter=Filter(must=conditions),
            exact=True,
        )
        return result.count

    async def collection_stats(self) -> dict[str, Any]:
        """Aggregate counts for the whole collection, no user filter — dashboard use.

        Returns totals plus active-item counts per memory layer.
        """
        not_deprecated = FieldCondition(key="is_deprecated", match=MatchValue(value=False))

        async def _count(conditions: list[Any]) -> int:
            result = await self._client.count(
                collection_name=self._collection_name,
                count_filter=Filter(must=conditions) if conditions else None,
                exact=True,
            )
            return result.count

        total = await _count([])
        active = await _count([not_deprecated])
        pinned = await _count(
            [not_deprecated, FieldCondition(key="is_pinned", match=MatchValue(value=True))]
        )
        by_layer: dict[str, int] = {}
        for layer in MemoryLayer:
            by_layer[layer.value] = await _count(
                [
                    not_deprecated,
                    FieldCondition(key="memory_layer", match=MatchValue(value=layer.value)),
                ]
            )

        return {
            "total": total,
            "active": active,
            "deprecated": total - active,
            "pinned": pinned,
            "by_layer": by_layer,
        }

    async def ping(self) -> bool:
        try:
            await self._client.get_collections()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.close()

