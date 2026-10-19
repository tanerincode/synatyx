from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any

from src.config import ConsolidationSettings
from src.models.context import ContextItem
from src.models.memory_layer import MemoryLayer
from src.models.relation import MemoryRelation
from src.storage.postgres import PostgresStorage
from src.storage.qdrant import QdrantStorage

logger = logging.getLogger(__name__)

# Episodic records that must never be merged away: attempts are structured
# tried-and-failed history, summarizer output is already a consolidation.
_SKIP_TYPES = {"attempt", "skill", "consolidated"}

# The shared L4 collection holds user preferences — nothing episodic to merge.
_SKIP_COLLECTIONS = {"ctx_users", "ctx_system"}


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def greedy_cluster(
    items: list[dict[str, Any]], threshold: float
) -> list[list[dict[str, Any]]]:
    """Group items whose embeddings are pairwise-similar to a cluster anchor.

    Greedy single-pass: the first unassigned item anchors a cluster and pulls
    in every remaining item within the similarity threshold. O(n²) — fine for
    the few hundred L2 items a user accumulates between runs.
    """
    clusters: list[list[dict[str, Any]]] = []
    assigned: set[str] = set()

    for anchor in items:
        if anchor["_id"] in assigned:
            continue
        cluster = [anchor]
        assigned.add(anchor["_id"])
        for candidate in items:
            if candidate["_id"] in assigned:
                continue
            if cosine(anchor["_vector"], candidate["_vector"]) >= threshold:
                cluster.append(candidate)
                assigned.add(candidate["_id"])
        clusters.append(cluster)

    return clusters


def merge_content(cluster: list[dict[str, Any]]) -> str:
    """Extractive merge: newest first, one bullet per source memory.

    Deliberately deterministic (no LLM dependency in the background job) —
    the point is one retrievable L3 fact instead of N near-duplicates; a
    smarter abstractive merge can replace this later without schema changes.
    """
    ordered = sorted(cluster, key=lambda i: i.get("created_at") or "", reverse=True)
    bullets = "\n".join(f"- {i.get('content', '').strip()}" for i in ordered)
    return f"[Consolidated from {len(cluster)} episodic memories]\n{bullets}"


def centroid(vectors: list[list[float]]) -> list[float]:
    n = len(vectors)
    dims = len(vectors[0])
    return [sum(v[i] for v in vectors) / n for i in range(dims)]


class Consolidator:
    """Sleep-style memory consolidation.

    Scans each project collection for clusters of similar episodic (L2)
    memories and merges every cluster of min_cluster_size+ into a single L3
    fact: the originals are deprecated (never deleted) and linked to the new
    item with `supersedes` edges, so history stays navigable.
    """

    def __init__(
        self,
        qdrant: QdrantStorage,
        postgres: PostgresStorage,
        settings: ConsolidationSettings,
    ) -> None:
        self._qdrant = qdrant
        self._postgres = postgres
        self._settings = settings

    async def run_once(self) -> dict[str, int]:
        totals = {"merged_clusters": 0, "items_consolidated": 0, "skipped_small": 0}
        if not self._settings.enabled:
            return totals

        collections = await self._qdrant.get_all_collections()
        for collection in collections:
            from src.core.project import is_index_collection
            if (
                collection in _SKIP_COLLECTIONS
                or not collection.startswith("ctx_")
                or is_index_collection(collection)
            ):
                continue
            scoped = self._qdrant.scoped(collection)
            stats = await self._process_collection(scoped, collection, totals)
            for k, v in stats.items():
                totals[k] += v
            if totals["merged_clusters"] >= self._settings.max_merges_per_run:
                logger.info("Consolidation: max_merges_per_run reached — stopping this pass")
                break

        logger.info("Consolidation pass complete: %s", totals)
        return totals

    async def _process_collection(
        self, storage: QdrantStorage, collection: str, running_totals: dict[str, int]
    ) -> dict[str, int]:
        stats = {"merged_clusters": 0, "items_consolidated": 0, "skipped_small": 0}

        # Collect all mergeable L2 items (paginated), grouped per user
        by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
        offset: str | None = None
        while True:
            items, offset = await storage.scan_all_items(
                memory_layer=MemoryLayer.L2,
                include_deprecated=False,
                limit=500,
                offset=offset,
                with_vectors=True,
            )
            for item in items:
                if item.get("is_pinned"):
                    continue
                meta = item.get("metadata") or {}
                if meta.get("type") in _SKIP_TYPES:
                    continue
                if "_vector" not in item or not item.get("user_id"):
                    continue
                by_user[item["user_id"]].append(item)
            if offset is None:
                break

        for user_id, user_items in by_user.items():
            clusters = greedy_cluster(user_items, self._settings.similarity_threshold)
            for cluster in clusters:
                if len(cluster) < self._settings.min_cluster_size:
                    stats["skipped_small"] += 1
                    continue
                merges_so_far = running_totals["merged_clusters"] + stats["merged_clusters"]
                if merges_so_far >= self._settings.max_merges_per_run:
                    return stats
                try:
                    await self._merge_cluster(storage, collection, user_id, cluster)
                    stats["merged_clusters"] += 1
                    stats["items_consolidated"] += len(cluster)
                except Exception:
                    logger.exception(
                        "Consolidation of a %d-item cluster failed in %s — skipping",
                        len(cluster), collection,
                    )

        return stats

    async def _merge_cluster(
        self,
        storage: QdrantStorage,
        collection: str,
        user_id: str,
        cluster: list[dict[str, Any]],
    ) -> None:
        source_ids = [i["_id"] for i in cluster]
        projects = [(i.get("metadata") or {}).get("project") for i in cluster]
        project = next((p for p in projects if p), None)
        session_ids = [i.get("session_id") for i in cluster if i.get("session_id")]

        merged = ContextItem(
            user_id=user_id,
            session_id=session_ids[0] if session_ids else None,
            content=merge_content(cluster),
            memory_layer=MemoryLayer.L3,
            importance=min(0.9, max(float(i.get("importance", 0.5)) for i in cluster)),
            # Embed at the cluster centroid — no embedder dependency in the
            # background job, and the centroid is exactly what these items
            # collectively matched on.
            embedding=centroid([i["_vector"] for i in cluster]),
            metadata={
                "type": "consolidated",
                "consolidated_from": source_ids,
                "origin": "agent-inferred",
                **({"project": project} if project else {}),
            },
        )
        await storage.upsert(merged)

        for source_id in source_ids:
            await storage.deprecate(source_id, reason="consolidated into L3")
            await self._postgres.relation_add(MemoryRelation(
                user_id=user_id,
                source_item_id=merged.id,
                target_item_id=source_id,
                relation_type="supersedes",
                project=project,
                metadata={"auto": True, "consolidation": True},
            ))

        logger.info(
            "Consolidated %d L2 items into L3 %s (%s)", len(cluster), merged.id, collection
        )
