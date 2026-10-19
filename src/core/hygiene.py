"""Memory hygiene: what GC is about to do, which memories look stale, and which
look like duplicates.

The counting used to live inline in the context_gc_stats tool. It is here so
the tool and the dashboard hygiene endpoint share one implementation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from src.config import GCSettings
from src.core.gc import GarbageCollector
from src.core.staleness import check_stale_files

logger = logging.getLogger(__name__)

# context_gc_stats has always reported the 14 day window; the dashboard shows 7.
GC_STATS_WARN_DAYS = 14
DASHBOARD_WARN_DAYS = 7
STALE_LIMIT = 50
DUPLICATE_LIMIT = 50


@dataclass
class GCCounts:
    total: int = 0
    protected: int = 0
    expiring_soon: int = 0
    deprecated: int = 0
    pending_hard_delete: int = 0


def _parse(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class HygieneService:
    def __init__(self, qdrant: Any, postgres: Any, gc_settings: GCSettings) -> None:
        self._postgres = postgres
        self._settings = gc_settings
        self._gc = GarbageCollector(qdrant=qdrant, postgres=postgres, settings=gc_settings)

    def gc_counts(
        self,
        items: list[dict[str, Any]],
        warn_days: int = GC_STATS_WARN_DAYS,
        now: datetime | None = None,
    ) -> GCCounts:
        """Classify scanned payloads the way the GC would.

        `expiring_soon` counts live, non-immune items whose remaining TTL is
        positive and at most `warn_days`.
        """
        now = now or datetime.now(UTC)
        warn_threshold = timedelta(days=warn_days)
        counts = GCCounts(total=len(items))

        for item in items:
            if item.get("is_deprecated"):
                counts.deprecated += 1
                dep_raw = item.get("deprecated_at")
                if dep_raw:
                    dep_at = _parse(dep_raw)
                    if (now - dep_at).days >= self._settings.grace_period_days:
                        counts.pending_hard_delete += 1
                continue

            if self._gc._is_immune(item):
                counts.protected += 1
                continue

            base_ttl = self._gc._get_base_ttl(item.get("memory_layer", ""))
            if base_ttl is None:
                counts.protected += 1
                continue

            effective_ttl = self._gc.effective_ttl(base_ttl, item)
            last_raw = item.get("last_accessed_at") or item.get("created_at")
            if last_raw:
                last = _parse(last_raw)
                remaining = timedelta(days=effective_ttl) - (now - last)
                if timedelta(0) < remaining <= warn_threshold:
                    counts.expiring_soon += 1
        return counts

    @staticmethod
    def stale_items(items: list[dict[str, Any]], limit: int = STALE_LIMIT) -> list[dict[str, Any]]:
        """Live items whose referenced files changed (or vanished) since storing.

        Same test as the possibly_stale flag on retrieval: check_stale_files.
        Returns payload copies carrying `possibly_stale` and `stale_files`.
        """
        out: list[dict[str, Any]] = []
        for item in items:
            if item.get("is_deprecated"):
                continue
            try:
                files = check_stale_files(item.get("metadata") or {})
            except Exception:  # a bad payload must not break the whole report
                logger.debug("Staleness check failed", exc_info=True)
                continue
            if files:
                out.append({**item, "possibly_stale": True, "stale_files": files})
                if len(out) >= limit:
                    break
        return out

    async def duplicates(
        self, items: list[dict[str, Any]], limit: int = DUPLICATE_LIMIT
    ) -> list[dict[str, Any]]:
        """Pairs of live items joined by an `alternative_to` relation edge.

        `similarity` is the value stored on the edge when the edge was created
        by auto-link (metadata.similarity); manually created edges carry none,
        so it is None. Both endpoints must be in `items`; the pair is reported
        once however many edges join it.
        """
        by_id = {p["_id"]: p for p in items if p.get("_id") and not p.get("is_deprecated")}
        if not by_id:
            return []
        edges = await self._postgres.relation_list_all(item_ids=list(by_id), limit=500)
        seen: set[frozenset[str]] = set()
        pairs: list[dict[str, Any]] = []
        for edge in edges:
            if edge.relation_type != "alternative_to":
                continue
            a, b = by_id.get(edge.source_item_id), by_id.get(edge.target_item_id)
            key = frozenset((edge.source_item_id, edge.target_item_id))
            if a is None or b is None or key in seen:
                continue
            seen.add(key)
            similarity = (edge.metadata or {}).get("similarity")
            pairs.append({"a": a, "b": b, "similarity": similarity})
        # most similar first; edges without a stored similarity last
        pairs.sort(key=lambda p: (p["similarity"] is None, -(p["similarity"] or 0.0)))
        return pairs[:limit]
