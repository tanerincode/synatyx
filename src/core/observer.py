from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from src.config import ObserverSettings
from src.core.consolidate import cosine
from src.models.relation import MemoryRelation
from src.storage.postgres import PostgresStorage
from src.storage.qdrant import QdrantStorage

logger = logging.getLogger(__name__)

_SKIP_COLLECTIONS = {"ctx_system"}
# Skill embeddings are descriptions for RAG matching, not memories.
_SKIP_TYPES = {"skill"}


class RelationObserver:
    """Background auto-relate pass.

    Scans each collection for pairs of similar same-user memories and links
    them with `related_to` edges. Deliberately conservative:

    - only `related_to` — similarity cannot infer semantic edge types
    - same user only — relations are user-scoped and must not leak structure
    - pairs already linked by ANY edge (either direction) are skipped, so
      store-time alternative detection and manual curation always win
    - per-item cap counts only observer-created edges, so manual edges never
      block discovery but the observer cannot build hairballs
    - every edge carries {auto, origin: "observer", score} provenance
    """

    def __init__(
        self,
        qdrant: QdrantStorage,
        postgres: PostgresStorage,
        settings: ObserverSettings,
    ) -> None:
        self._qdrant = qdrant
        self._postgres = postgres
        self._settings = settings

    async def run_once(self) -> dict[str, int]:
        totals = {
            "edges_created": 0,
            "candidates": 0,
            "skipped_existing": 0,
            "skipped_capped": 0,
        }
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
            try:
                stats = await self._process_collection(scoped, collection, totals)
            except Exception:
                logger.exception("Observer failed on %s — skipping collection", collection)
                continue
            for k, v in stats.items():
                totals[k] += v
            if totals["edges_created"] >= self._settings.max_edges_per_run:
                logger.info("Observer: max_edges_per_run reached — stopping this pass")
                break

        logger.info("Observer pass complete: %s", totals)
        return totals

    async def _process_collection(
        self, storage: QdrantStorage, collection: str, running_totals: dict[str, int]
    ) -> dict[str, int]:
        stats = {
            "edges_created": 0,
            "candidates": 0,
            "skipped_existing": 0,
            "skipped_capped": 0,
        }

        by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
        offset: str | None = None
        while True:
            items, offset = await storage.scan_all_items(
                include_deprecated=False,
                limit=500,
                offset=offset,
                with_vectors=True,
            )
            for item in items:
                meta = item.get("metadata") or {}
                if meta.get("type") in _SKIP_TYPES or item.get("type") in _SKIP_TYPES:
                    continue
                if "_vector" not in item or not item.get("user_id"):
                    continue
                by_user[item["user_id"]].append(item)
            if offset is None:
                break

        for user_id, user_items in by_user.items():
            if len(user_items) < 2:
                continue
            created = await self._relate_user_items(
                collection, user_id, user_items, stats, running_totals
            )
            stats["edges_created"] += created

        return stats

    async def _relate_user_items(
        self,
        collection: str,
        user_id: str,
        items: list[dict[str, Any]],
        stats: dict[str, int],
        running_totals: dict[str, int],
    ) -> int:
        # Candidate pairs above the threshold, strongest first, so the
        # per-item cap keeps the best links when a cluster is dense.
        candidates: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                score = cosine(items[i]["_vector"], items[j]["_vector"])
                if score >= self._settings.similarity_threshold:
                    candidates.append((score, items[i], items[j]))
        if not candidates:
            return 0
        candidates.sort(key=lambda c: c[0], reverse=True)
        stats["candidates"] += len(candidates)

        # Existing edges among this user's items: linked pairs are skipped
        # outright; observer-origin edges count toward the per-item cap.
        existing = await self._postgres.relation_list_all(
            item_ids=[i["_id"] for i in items], limit=1000
        )
        linked_pairs = {
            frozenset((e.source_item_id, e.target_item_id)) for e in existing
        }
        observer_degree: dict[str, int] = defaultdict(int)
        for e in existing:
            if (e.metadata or {}).get("origin") == "observer":
                observer_degree[e.source_item_id] += 1
                observer_degree[e.target_item_id] += 1

        created = 0
        for score, a, b in candidates:
            total_created = (
                running_totals["edges_created"] + stats["edges_created"] + created
            )
            if total_created >= self._settings.max_edges_per_run:
                break

            pair = frozenset((a["_id"], b["_id"]))
            if pair in linked_pairs:
                stats["skipped_existing"] += 1
                continue
            cap = self._settings.max_edges_per_item
            if observer_degree[a["_id"]] >= cap or observer_degree[b["_id"]] >= cap:
                stats["skipped_capped"] += 1
                continue

            # Newer item points at the older one — deterministic direction.
            source, target = (
                (a, b) if (a.get("created_at") or "") >= (b.get("created_at") or "") else (b, a)
            )
            if self._settings.dry_run:
                logger.info(
                    "Observer dry-run: would relate %s -> %s (score=%.3f, %s)",
                    source["_id"], target["_id"], score, collection,
                )
            else:
                project = (source.get("metadata") or {}).get("project") or source.get("project")
                await self._postgres.relation_add(MemoryRelation(
                    user_id=user_id,
                    source_item_id=source["_id"],
                    target_item_id=target["_id"],
                    relation_type="related_to",
                    project=project,
                    metadata={"auto": True, "origin": "observer", "score": round(score, 4)},
                ))
            linked_pairs.add(pair)
            observer_degree[a["_id"]] += 1
            observer_degree[b["_id"]] += 1
            created += 1

        return created
