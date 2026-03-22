from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config import GCSettings
from src.models.memory_layer import MemoryLayer
from src.storage.postgres import PostgresStorage
from src.storage.qdrant import QdrantStorage

logger = logging.getLogger(__name__)

# Items matching any of these conditions are never auto-expired
_IMMUNE_LAYERS = {MemoryLayer.L4.value}
_IMMUNE_TYPE = "skill"


class GarbageCollector:
    def __init__(
        self,
        qdrant: QdrantStorage,
        postgres: PostgresStorage,
        settings: GCSettings,
    ) -> None:
        self._qdrant = qdrant
        self._postgres = postgres
        self._settings = settings

    # ── Public ───────────────────────────────────────────────────────────────

    async def run_once(self) -> dict[str, int]:
        """Execute one full GC pass across all Qdrant collections.

        Returns a summary dict: {deprecated, deleted, skipped}.
        """
        run_id = str(uuid.uuid4())
        logger.info("GC run %s started", run_id)

        collections = await self._qdrant.get_all_collections()
        totals = {"deprecated": 0, "deleted": 0, "skipped": 0}

        for collection in collections:
            # Swap to correct collection scope
            self._qdrant._collection_name = collection  # type: ignore[attr-defined]
            stats = await self._process_collection(collection, run_id)
            for k, v in stats.items():
                totals[k] += v

        logger.info(
            "GC run %s finished — deprecated=%d deleted=%d skipped=%d",
            run_id,
            totals["deprecated"],
            totals["deleted"],
            totals["skipped"],
        )
        return totals

    # ── Internals ────────────────────────────────────────────────────────────

    async def _process_collection(self, collection: str, run_id: str) -> dict[str, int]:
        stats = {"deprecated": 0, "deleted": 0, "skipped": 0}
        offset: str | None = None
        now = datetime.now(timezone.utc)

        # Phase 2: hard-delete already-deprecated items past grace period
        deprecated_items, _ = await self._qdrant.scan_all_items(
            include_deprecated=True, limit=500
        )
        for item in deprecated_items:
            if not item.get("is_deprecated"):
                continue
            deprecated_at_raw = item.get("deprecated_at")
            if not deprecated_at_raw:
                continue
            deprecated_at = datetime.fromisoformat(deprecated_at_raw)
            if deprecated_at.tzinfo is None:
                deprecated_at = deprecated_at.replace(tzinfo=timezone.utc)
            if (now - deprecated_at).days >= self._settings.grace_period_days:
                item_id = item["_id"]
                await self._qdrant.hard_delete(item_id)
                await self._postgres.gc_log_add(
                    run_id=run_id, item_id=item_id, collection=collection,
                    memory_layer=item.get("memory_layer", "unknown"),
                    action="deleted", reason="grace period expired",
                )
                stats["deleted"] += 1

        # Phase 1: deprecate expired items
        while True:
            items, offset = await self._qdrant.scan_all_items(
                include_deprecated=False, limit=500, offset=offset
            )
            for item in items:
                result = await self._process_item(item, collection, run_id, now)
                stats[result] += 1
            if offset is None:
                break

        return stats

    async def _process_item(
        self, item: dict[str, Any], collection: str, run_id: str, now: datetime
    ) -> str:
        item_id = item["_id"]

        if self._is_immune(item):
            return "skipped"

        base_ttl = self._get_base_ttl(item.get("memory_layer", ""))
        if base_ttl is None:
            return "skipped"

        importance = float(item.get("importance", 0.5))
        effective_ttl = base_ttl * (1 + importance * self._settings.importance_multiplier)

        last_touched_raw = item.get("last_accessed_at") or item.get("created_at")
        if not last_touched_raw:
            return "skipped"

        last_touched = datetime.fromisoformat(last_touched_raw)
        if last_touched.tzinfo is None:
            last_touched = last_touched.replace(tzinfo=timezone.utc)

        if (now - last_touched) < timedelta(days=effective_ttl):
            return "skipped"

        await self._qdrant.deprecate(item_id, reason="ttl expired")
        await self._postgres.gc_log_add(
            run_id=run_id, item_id=item_id, collection=collection,
            memory_layer=item.get("memory_layer", "unknown"),
            action="deprecated",
            reason=f"not accessed for {int(effective_ttl)} days",
        )
        return "deprecated"

    def _is_immune(self, item: dict[str, Any]) -> bool:
        return (
            item.get("is_pinned") is True
            or item.get("memory_layer") in _IMMUNE_LAYERS
            or float(item.get("importance", 0)) >= 1.0
            or (item.get("metadata") or {}).get("type") == _IMMUNE_TYPE
            or item.get("type") == _IMMUNE_TYPE
        )

    def _get_base_ttl(self, memory_layer: str) -> float | None:
        if memory_layer == MemoryLayer.L2.value:
            return float(self._settings.l2_base_ttl_days)
        if memory_layer == MemoryLayer.L3.value:
            return float(self._settings.l3_base_ttl_days)
        return None  # L1 handled by Redis TTL; L4 immune

