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

            # Keep the Postgres session record in sync
            if session_id:
                await self._upsert_session(user_id, session_id, item.token_estimate)

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

    async def checkpoint(
        self,
        name: str,
        content: str,
        user_id: str,
        project: str | None = None,
        session_id: str | None = None,
    ) -> tuple[str, bool]:
        """
        Store a named checkpoint as a pinned L3 memory item with importance=1.0.

        Checkpoints represent significant development milestones or decisions.
        They are never excluded from retrieval and can be deprecated (not deleted)
        when superseded.
        """
        metadata: dict[str, Any] = {"checkpoint_name": name}
        if project:
            metadata["project"] = project

        return await self.store(
            content=f"[Checkpoint: {name}]\n\n{content}",
            user_id=user_id,
            memory_layer=MemoryLayer.L3,
            importance=1.0,
            is_pinned=True,
            session_id=session_id,
            metadata=metadata,
        )

    async def deprecate(
        self,
        item_id: str,
        user_id: str,
        reason: str | None = None,
    ) -> None:
        """
        Mark an existing memory item as deprecated.

        The item is NOT deleted — it stays in the vector store but is excluded
        from normal retrieval (search filters out is_deprecated=True).
        A deprecation note is optionally stored alongside it.
        """
        # Fetch the item first to enforce user isolation
        items = await self._qdrant.list_items(
            user_id=user_id, include_deprecated=True, limit=1000
        )
        target = next((i for i in items if i.id == item_id), None)
        if target is None:
            raise ValueError(f"Item {item_id!r} not found for user {user_id!r}")
        if target.user_id != user_id:
            raise PermissionError(f"User isolation violation for item {item_id!r}")

        await self._qdrant.deprecate(item_id, reason=reason)

        await self._postgres.audit(user_id, "context_deprecate", {
            "item_id": item_id,
            "reason": reason,
        })

    async def _upsert_session(self, user_id: str, session_id: str, token_delta: int) -> None:
        """Create or update the Postgres session record for L1 tracking."""
        from src.models.session import Session
        try:
            session = await self._postgres.session_get(session_id)
            if session is None:
                session = Session(
                    session_id=session_id,
                    user_id=user_id,
                    token_count=token_delta,
                    message_count=1,
                )
                await self._postgres.session_create(session)
            else:
                session.token_count += token_delta
                session.message_count += 1
                session.touch()
                await self._postgres.session_update(session)
        except Exception:
            # Postgres being down must not break L1 storage
            import logging
            logging.getLogger(__name__).warning(
                "Could not upsert session %s for user %s — Postgres unavailable", session_id, user_id
            )

