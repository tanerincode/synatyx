from __future__ import annotations

import logging
from dataclasses import dataclass

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
    ) -> IngestResult:
        parser = get_parser(source)
        chunks = await parser.parse(source)

        stored = 0
        failed = 0

        for chunk in chunks:
            if chunk.is_empty:
                continue
            try:
                metadata: dict = {**chunk.metadata, "source": source}
                if project:
                    metadata["project"] = project
                if chunk.title:
                    metadata["section"] = chunk.title

                await self._store.store(
                    content=chunk.content,
                    user_id=user_id,
                    memory_layer=memory_layer,
                    importance=importance,
                    session_id=session_id,
                    metadata=metadata,
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

