from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from src.config import GCSettings
from src.core.retention import KEEP_FOREVER, policy_for
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
        self._retention = settings.retention_policy_list

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
            # Code/doc index collections are managed by context_index — TTL
            # decay must never touch them.
            from src.core.project import is_index_collection
            if is_index_collection(collection):
                continue
            # Use a collection-scoped view — never mutate the shared storage
            scoped = self._qdrant.scoped(collection)
            stats = await self._process_collection(scoped, collection, run_id)
            for k, v in stats.items():
                totals[k] += v

        # Usage metering rows are append-only telemetry — cap their history
        from src.config import settings as _app_settings
        retention = _app_settings.usage.retention_days
        if retention > 0:
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(days=retention)
                pruned = await self._postgres.usage_prune(before=cutoff)
                if pruned:
                    logger.info("GC run %s pruned %d tool_usage rows older than %dd", run_id, pruned, retention)
            except Exception:
                logger.exception("tool_usage prune failed")

        # Anonymous OAuth registrations that never completed a handshake are
        # also pruned lazily on /register; this keeps the table bounded even
        # when nobody registers for a while.
        try:
            stale = await self._postgres.oauth_client_prune_unused(
                _app_settings.oauth.unused_client_ttl_seconds
            )
            if stale:
                logger.info("GC run %s pruned %d unused OAuth client registrations", run_id, stale)
        except Exception:
            logger.exception("oauth_clients prune failed")

        logger.info(
            "GC run %s finished — deprecated=%d deleted=%d skipped=%d",
            run_id,
            totals["deprecated"],
            totals["deleted"],
            totals["skipped"],
        )
        return totals

    # ── Internals ────────────────────────────────────────────────────────────

    async def _process_collection(
        self, qdrant: QdrantStorage, collection: str, run_id: str
    ) -> dict[str, int]:
        stats = {"deprecated": 0, "deleted": 0, "skipped": 0}
        now = datetime.now(timezone.utc)

        # Phase 2: hard-delete already-deprecated items past grace period
        offset: str | None = None
        while True:
            deprecated_items, offset = await qdrant.scan_all_items(
                include_deprecated=True, limit=500, offset=offset
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
                    await qdrant.hard_delete(item_id)
                    await self._postgres.relation_delete_for_items([item_id])
                    await self._postgres.gc_log_add(
                        run_id=run_id, item_id=item_id, collection=collection,
                        memory_layer=item.get("memory_layer", "unknown"),
                        action="deleted", reason="grace period expired",
                    )
                    stats["deleted"] += 1
            if offset is None:
                break

        # Phase 1: deprecate expired items
        offset = None
        while True:
            items, offset = await qdrant.scan_all_items(
                include_deprecated=False, limit=500, offset=offset
            )
            for item in items:
                result = await self._process_item(qdrant, item, collection, run_id, now)
                stats[result] += 1
            if offset is None:
                break

        return stats

    async def _process_item(
        self,
        qdrant: QdrantStorage,
        item: dict[str, Any],
        collection: str,
        run_id: str,
        now: datetime,
    ) -> str:
        item_id = item["_id"]

        # Retention is checked before anything else, and overrides every
        # exemption below it. A promise that data is gone after 90 days cannot
        # make an exception for the records someone pinned or scored as
        # important — those are exactly the ones a person asking about their
        # data would care about.
        retention = self._retention_verdict(item, now)
        if retention is not None:
            if retention == "keep":
                return "skipped"
            await qdrant.deprecate(item_id, reason="retention policy")
            await self._postgres.gc_log_add(
                run_id=run_id, item_id=item_id, collection=collection,
                memory_layer=item.get("memory_layer", "unknown"),
                action="deprecated", reason=retention,
            )
            return "deprecated"

        if self._is_immune(item):
            return "skipped"

        base_ttl = self._get_base_ttl(item.get("memory_layer", ""))
        if base_ttl is None:
            return "skipped"

        effective_ttl = self.effective_ttl(base_ttl, item)

        last_touched_raw = item.get("last_accessed_at") or item.get("created_at")
        if not last_touched_raw:
            return "skipped"

        last_touched = datetime.fromisoformat(last_touched_raw)
        if last_touched.tzinfo is None:
            last_touched = last_touched.replace(tzinfo=timezone.utc)

        if (now - last_touched) < timedelta(days=effective_ttl):
            return "skipped"

        await qdrant.deprecate(item_id, reason="ttl expired")
        await self._postgres.gc_log_add(
            run_id=run_id, item_id=item_id, collection=collection,
            memory_layer=item.get("memory_layer", "unknown"),
            action="deprecated",
            reason=f"not accessed for {int(effective_ttl)} days",
        )
        return "deprecated"

    def _retention_verdict(self, item: dict[str, Any], now: datetime) -> str | None:
        """"keep", a reason to expire now, or None when no policy applies.

        Age is measured from creation, never from last access: a conversation
        that someone re-reads on day 89 is not thereby allowed to outlive the
        retention window.
        """
        policy = policy_for(self._retention, item.get("project"))
        if policy is None:
            return None

        limit = policy.days_for(str(item.get("memory_layer") or ""))
        if limit is None:
            return None
        if limit == KEEP_FOREVER:
            return "keep"

        created_raw = item.get("created_at")
        if not created_raw:
            # Undatable under a retention policy: expire it rather than keep
            # something whose age cannot be shown to be within the promise.
            return "retention policy (no creation date)"

        created = datetime.fromisoformat(created_raw)
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)

        days = float(limit)
        if (now - created) < timedelta(days=days):
            return "keep"
        return f"retention policy: older than {int(days)} days"

    def effective_ttl(self, base_ttl: float, item: dict[str, Any]) -> float:
        """TTL after importance scaling and type-aware decay.

        effective = base × (1 + importance × importance_multiplier) × type_multiplier

        where type_multiplier comes from settings.fact_type_multipliers keyed
        by metadata.fact_type — file locations rot fast, preferences slowly.
        Untyped items keep the classic importance-only behaviour.
        """
        importance = float(item.get("importance", 0.5))
        ttl = base_ttl * (1 + importance * self._settings.importance_multiplier)

        fact_type = (item.get("metadata") or {}).get("fact_type")
        if fact_type:
            ttl *= self._settings.fact_type_multipliers.get(fact_type, 1.0)
        return ttl

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

