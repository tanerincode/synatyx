from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.core.chunker import default_chunker
from src.core.store import StoreService
from src.models.memory_layer import MemoryLayer
from src.parsers.registry import get_parser

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    source: str
    chunks_stored: int
    chunks_failed: int
    total_chunks: int


class IngestService:
    """
    Parse any supported source (file path or URL) into chunks
    and store each one via StoreService.
    """

    def __init__(self, store: StoreService) -> None:
        self._store = store

    async def ingest(
        self,
        source: str,
        user_id: str,
        memory_layer: MemoryLayer = MemoryLayer.L3,
        importance: float = 0.8,
        project: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> IngestResult:
        """Parse a file path or URL into chunks and store each one.

        `metadata` is merged into every chunk's own metadata — the caller's
        source id, title, locale and so on. It is what makes a chunk citable
        later: a retrieval result can only name the document it came from if
        the document's identity travelled with it at ingestion time.
        """
        parser = get_parser(source)
        chunks = await parser.parse(source)

        # Provenance: ingested content is external data, not something the
        # user or agent asserted — tag it so retrieval can flag trust level.
        origin = (
            "ingested-from-web"
            if source.startswith(("http://", "https://"))
            else "ingested-from-file"
        )

        stored = 0
        failed = 0

        for chunk in chunks:
            if chunk.is_empty:
                continue
            try:
                # Caller metadata first, so a chunk's own parsed values (its
                # section title, its offsets) win over a document-level default
                # rather than being overwritten by it.
                chunk_metadata: dict[str, Any] = {
                    **(metadata or {}),
                    **chunk.metadata,
                    "source": source,
                }
                if project:
                    chunk_metadata["project"] = project
                if chunk.title:
                    chunk_metadata["section"] = chunk.title

                await self._store.store(
                    content=chunk.content,
                    user_id=user_id,
                    memory_layer=memory_layer,
                    importance=importance,
                    session_id=session_id,
                    metadata=chunk_metadata,
                    origin=origin,
                )
                stored += 1
                logger.debug("Ingested chunk %d/%d from %s", stored, len(chunks), source)
            except Exception:
                logger.exception("Failed to store chunk %r from %s", chunk.title, source)
                failed += 1

        logger.info(
            "Ingest complete: %s → %d stored, %d failed (total %d)",
            source, stored, failed, len(chunks)
        )
        return IngestResult(
            source=source,
            chunks_stored=stored,
            chunks_failed=failed,
            total_chunks=len(chunks),
        )

    async def ingest_text(
        self,
        text: str,
        user_id: str,
        source: str,
        memory_layer: MemoryLayer = MemoryLayer.L3,
        importance: float = 0.8,
        project: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        origin: str = "ingested-from-file",
    ) -> IngestResult:
        """Chunk and store text the caller already holds.

        The parser path needs something fetchable — a file on this machine or a
        URL this server can reach. A caller that has already fetched, rendered
        or assembled the document has neither, and pushing the text is both
        cheaper and the only option when the content never existed as a
        retrievable document in the first place.

        `source` is the caller's name for where the text came from, recorded
        exactly as the parser path records a path or URL, so both kinds of
        ingest are addressable the same way afterwards.
        """
        chunks = default_chunker.chunk(text)

        stored = 0
        failed = 0

        for chunk in chunks:
            if not chunk.text.strip():
                continue
            try:
                chunk_metadata: dict[str, Any] = {
                    **(metadata or {}),
                    "source": source,
                    "chunk_index": chunk.index,
                    # Offsets into the text as submitted, so a citation can
                    # point at the passage inside the caller's own copy of the
                    # document rather than only naming the document.
                    "offset_start": chunk.start_pos,
                    "offset_end": chunk.end_pos,
                }
                if project:
                    chunk_metadata["project"] = project

                await self._store.store(
                    content=chunk.text,
                    user_id=user_id,
                    memory_layer=memory_layer,
                    importance=importance,
                    session_id=session_id,
                    metadata=chunk_metadata,
                    origin=origin,
                )
                stored += 1
            except Exception:
                logger.exception("Failed to store chunk %d of %s", chunk.index, source)
                failed += 1

        logger.info(
            "Text ingest complete: %s → %d stored, %d failed (total %d)",
            source, stored, failed, len(chunks),
        )
        return IngestResult(
            source=source,
            chunks_stored=stored,
            chunks_failed=failed,
            total_chunks=len(chunks),
        )

