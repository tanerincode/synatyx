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

# Provenance values — retrieved memory is injected into the agent's context,
# so the agent must be able to tell trusted user statements from content that
# arrived via ingestion of external sources (prompt-injection surface).
KNOWN_ORIGINS = (
    "user-stated",         # the user said it directly
    "agent-inferred",      # the agent concluded it (default)
    "ingested-from-file",  # context_ingest on a local file
    "ingested-from-web",   # context_ingest on a URL — treat as untrusted data
    "web-search",          # found via web search — treat as untrusted data
)
DEFAULT_ORIGIN = "agent-inferred"

# Prompt injection patterns to sanitize from stored content
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
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
        origin: str | None = None,
    ) -> tuple[list[str], bool]:
        """
        Store content into the appropriate memory layer.

        For vector layers (L2, L3, L4):
        - Content longer than CHUNK_THRESHOLD is split into chunks first
        - Each chunk is embedded and stored as a separate ContextItem

        Returns:
            item_ids: IDs of all stored items (one per chunk, first chunk first)
            embedded: whether content was vectorized and stored in Qdrant
        """
        sanitized = _sanitize(content)
        embedded = False
        base_meta = {**(metadata or {}), "confidence": confidence}
        # Provenance is mandatory: explicit param wins, then caller metadata,
        # then the default. Every stored item carries an origin.
        base_meta["origin"] = origin or base_meta.get("origin") or DEFAULT_ORIGIN

        # Staleness anchors: when the caller lists the files this fact refers
        # to (metadata.files), hash them now so retrieval can detect that the
        # ground truth changed underneath the memory.
        if isinstance(base_meta.get("files"), list) and "file_hashes" not in base_meta:
            from src.core.staleness import compute_file_hashes
            try:
                hashes = compute_file_hashes(base_meta["files"])
                if hashes:
                    base_meta["file_hashes"] = hashes
            except Exception:
                import logging
                logging.getLogger(__name__).debug("File hashing failed", exc_info=True)

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
            item_ids = [item.id]

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

            item_ids = []
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
                item_ids.append(item.id)

            embedded = True

        await self._redis.publish("context_stored", {
            "item_id": item_ids[0],
            "user_id": user_id,
            "memory_layer": memory_layer.value,
            "embedded": embedded,
        })

        await self._postgres.audit(user_id, "context_store", {
            "item_id": item_ids[0],
            "memory_layer": memory_layer.value,
            "importance": importance,
            "embedded": embedded,
        })

        return item_ids, embedded

    async def store_batch(
        self,
        items: list[dict[str, Any]],
        user_id: str,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Store multiple items in one call — one round-trip for the client.

        Each entry: {content, memory_layer, importance?, metadata?, confidence?}.
        Items are stored independently; a failure on one item does not roll back
        the others. Returns one result dict per input item, in order.
        """
        results: list[dict[str, Any]] = []
        for entry in items:
            try:
                item_ids, embedded = await self.store(
                    content=entry["content"],
                    user_id=user_id,
                    memory_layer=MemoryLayer(entry["memory_layer"]),
                    importance=float(entry.get("importance", 0.5)),
                    session_id=entry.get("session_id", session_id),
                    metadata=entry.get("metadata"),
                    confidence=float(entry.get("confidence", 1.0)),
                    origin=entry.get("origin"),
                )
                results.append({"item_id": item_ids[0], "item_ids": item_ids, "embedded": embedded})
            except Exception as exc:
                results.append({"error": str(exc)})
        return results

    async def checkpoint(
        self,
        name: str,
        content: str,
        user_id: str,
        project: str | None = None,
        session_id: str | None = None,
    ) -> tuple[list[str], bool]:
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
        # Fetch the item directly by ID to enforce user isolation
        target = await self._qdrant.get_by_id(item_id)
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
                "Could not upsert session %s for user %s — Postgres unavailable",
                session_id,
                user_id,
            )

