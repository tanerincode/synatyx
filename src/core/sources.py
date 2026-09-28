from __future__ import annotations

import logging

from qdrant_client.models import FieldCondition, MatchValue

from src.storage.qdrant import QdrantStorage

logger = logging.getLogger(__name__)

# Payload path of the caller's source id. Metadata is stored as a nested object
# on the point, so the filter key is dotted rather than top-level.
SOURCE_ID_KEY = "metadata.source_id"

# Points fetched per scroll page. Ids only, so a large source costs round trips
# rather than memory.
_SCROLL_PAGE = 512


class SourceService:
    """
    Source-scoped operations over ingested knowledge.

    A "source" is the caller's own identifier for a document it keeps
    elsewhere — a help-centre article, an uploaded file, a crawled page. Synatyx
    stores it on every chunk that came from that document, which is what makes
    "re-sync this article" and "this article is gone" expressible at all: a
    caller that can only address individual item ids has no way to say either,
    because it never learns the ids of the chunks its document became.
    """

    def __init__(self, storage: QdrantStorage) -> None:
        self._storage = storage

    async def item_ids_for_source(
        self,
        user_id: str,
        source_id: str,
        *,
        include_deprecated: bool = False,
    ) -> list[str]:
        """Every item id belonging to one source, newest scroll order."""
        conditions = [
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
            FieldCondition(key=SOURCE_ID_KEY, match=MatchValue(value=source_id)),
        ]
        if not include_deprecated:
            conditions.append(
                FieldCondition(key="is_deprecated", match=MatchValue(value=False))
            )

        ids: list[str] = []
        offset = None
        while True:
            records, offset = await self._storage.scroll_by_conditions(
                conditions, limit=_SCROLL_PAGE, offset=offset, with_payload=False
            )
            ids.extend(str(record.id) for record in records)
            if offset is None:
                break
        return ids

    async def deprecate_source(
        self, user_id: str, source_id: str, reason: str | None = None
    ) -> int:
        """
        Deprecate every live item from one source. Returns how many were
        affected — zero when the source was already gone.

        A count rather than a boolean on purpose: deprecating a source that
        does not exist is a no-op, and a caller re-running a sync needs to see
        that as "nothing there" instead of as success. Items are deprecated,
        never deleted, which is the same contract context_deprecate already
        offers — the history stays readable and a supersedes chain still
        resolves.
        """
        item_ids = await self.item_ids_for_source(user_id, source_id)
        deprecated = 0
        for item_id in item_ids:
            try:
                await self._storage.deprecate(
                    item_id, reason=reason or f"source {source_id!r} deprecated"
                )
                deprecated += 1
            except Exception:
                logger.exception(
                    "Failed to deprecate item %s of source %r", item_id, source_id
                )
        logger.info(
            "Deprecated %d/%d items of source %r", deprecated, len(item_ids), source_id
        )
        return deprecated


async def erase_user(storage: QdrantStorage, user_id: str) -> dict[str, object]:
    """
    Remove a user's vector-stored items from every collection.

    This is a hard delete, not a deprecation: it exists for erasure requests,
    where "still there but flagged" is not an answer. It sweeps every
    collection the server knows about — project collections, the shared L4
    collection, and the per-project code indexes — because a user id can appear
    in any of them and an erasure that misses one is not an erasure.

    Scope worth stating plainly: this covers the vector store. Rows a user id
    also appears on outside it are not touched here.
    """
    collections = await storage.get_all_collections()
    purged: list[str] = []
    failed: list[str] = []

    for name in collections:
        try:
            await storage.scoped(name).delete_by_user(user_id)
            purged.append(name)
        except Exception:
            logger.exception("Failed to erase user %r from collection %r", user_id, name)
            failed.append(name)

    logger.info("Erased user %r from %d collections", user_id, len(purged))
    return {"collections_purged": purged, "collections_failed": failed}
