from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

from src.core.bm25 import BM25Index
from src.core.budget import BudgetAllocation, BudgetManager
from src.core.embedder import get_embedder
from src.core.mmr import apply_mmr, diversify_by_source
from src.core.score import score_items
from src.models.context import ContextItem, ScoredContextItem
from src.models.memory_layer import MemoryLayer
from src.storage.postgres import PostgresStorage
from src.storage.qdrant import QdrantStorage
from src.storage.redis import RedisStorage

# Text-relevance fusion weights (from CTX-EG: 0.6 dense + 0.4 BM25)
DENSE_WEIGHT = 0.6
BM25_WEIGHT = 0.4

# Final blend: hybrid text relevance vs. contextual signals (recency,
# importance, user signal). final = 0.6 * text + 0.4 * signals.
TEXT_WEIGHT = 0.6
SIGNAL_WEIGHT = 1 - TEXT_WEIGHT

# MMR lambda: 0.6 = slightly favour relevance over diversity
MMR_LAMBDA = 0.6


@dataclass
class RetrieveResult:
    context_items: list[ScoredContextItem]
    total_tokens: int
    suggested_budget: dict[str, int]


def empty_retrieve_diagnostics(
    total_for_user: int,
    items_by_layer: dict[str, int],
    requested_layers: list[MemoryLayer],
    session_id: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Explain WHY a retrieve came back empty.

    An empty result is ambiguous to the caller: "nothing stored" and "query
    or filters missed" demand opposite next actions. This distinguishes them
    using collection counts (L1 lives in Redis and is not counted here).
    """
    filters = [
        name for name, value in (("session_id", session_id), ("project", project)) if value
    ]
    layer_names = ", ".join(l.value for l in requested_layers) or "none"

    if total_for_user == 0:
        hint = (
            "This user has no memories in the active project collection yet — "
            "nothing can match any query. Store facts with context_store first."
        )
    elif filters:
        hint = (
            f"{total_for_user} memories exist for this user, but none matched the "
            f"{' + '.join(filters)} filter(s). Retry without the filter(s) or check the values."
        )
    else:
        hint = (
            f"{total_for_user} memories exist for this user, but none are in the "
            f"requested layers ({layer_names}). Retry with other memory_layers."
        )

    return {
        "matched": 0,
        "total_items_for_user": total_for_user,
        "items_by_layer": items_by_layer,
        "filters_applied": filters,
        "requested_layers": [l.value for l in requested_layers],
        "hint": hint,
    }


class RetrieveService:
    def __init__(
        self,
        qdrant: QdrantStorage,
        redis: RedisStorage,
        postgres: PostgresStorage,
        budget_manager: BudgetManager | None = None,
    ) -> None:
        self._qdrant = qdrant
        self._redis = redis
        self._postgres = postgres
        self._embedder = get_embedder()
        self._budget = budget_manager or BudgetManager()
        # Strong refs to fire-and-forget tasks — the event loop only keeps weak
        # refs, so untracked tasks can be garbage-collected mid-flight
        self._bg_tasks: set[asyncio.Task[None]] = set()

    async def retrieve(
        self,
        query: str,
        user_id: str,
        session_id: str | None = None,
        project: str | None = None,
        top_k: int = 10,
        memory_layers: list[MemoryLayer] | None = None,
        use_mmr: bool = True,
        query_embedding: list[float] | None = None,
        metadata_filters: dict[str, Any] | None = None,
    ) -> RetrieveResult:
        """
        Hybrid retrieval: dense kNN + BM25 score fusion + MMR diversification.

        Pipeline (from CTX-EG + Synatyx combined):
        1. L1 from Redis (exact, no vector search)
        2. L2/L3/L4 from Qdrant (dense kNN — fetch 3x top_k as candidates)
        3. BM25 re-score candidates against query
        4. Fuse text relevance: text = 0.6 * dense + 0.4 * BM25
        5. Blend with contextual signals (recency, importance, user signal):
           final = 0.6 * text + 0.4 * signals
        6. Enforce token budget per layer
        7. Apply MMR diversification
        """
        layers = memory_layers or list(MemoryLayer)
        if query_embedding is None:
            query_embedding = await self._embedder.embed(query)

        all_items: list[ContextItem] = []

        # Step 1 — L1 working memory from Redis
        if MemoryLayer.L1 in layers and session_id:
            l1_items = await self._redis.l1_get(user_id, session_id)
            all_items.extend(l1_items)

        # Step 2 — Fetch 3x candidates from Qdrant for better BM25 + MMR pool.
        # Layer searches are independent — run them concurrently.
        candidate_k = top_k * 3
        vector_layers = [l for l in layers if l != MemoryLayer.L1]
        layer_results = await asyncio.gather(*(
            self._qdrant.search(
                query_vector=query_embedding,
                user_id=user_id,
                top_k=candidate_k,
                memory_layer=layer,
                session_id=session_id,
                project=project,
                metadata_filters=metadata_filters,
            )
            for layer in vector_layers
        ))
        hit_ids: list[str] = []
        for results in layer_results:
            all_items.extend(results)
            hit_ids.extend(r.id for r in results)
        # Fire-and-forget access tracking — update last_accessed_at in Qdrant
        if hit_ids:
            task = asyncio.create_task(self._track_access(hit_ids))
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)

        # Deduplicate
        seen: set[str] = set()
        unique_items: list[ContextItem] = []
        for item in all_items:
            if item.id not in seen:
                seen.add(item.id)
                unique_items.append(item)

        if not unique_items:
            allocation = self._budget.get_allocation()
            return RetrieveResult(context_items=[], total_tokens=0, suggested_budget=allocation.to_dict())

        # Step 3 — BM25 re-score all candidates
        bm25_index = BM25Index([item.content for item in unique_items])
        bm25_scores = bm25_index.score_all(query)

        # Normalize BM25 scores to [0, 1]
        max_bm25 = max(bm25_scores) if bm25_scores else 1.0
        if max_bm25 == 0:
            max_bm25 = 1.0
        bm25_norm = [s / max_bm25 for s in bm25_scores]

        # Step 4 — Score fusion + existing signals
        scored, _ = score_items(unique_items, query, query_embedding)

        for i, item in enumerate(scored):
            # Fuse dense score (already in item.semantic_score) with BM25,
            # then blend with contextual signals (recency, importance, user signal)
            text_score = DENSE_WEIGHT * item.semantic_score + BM25_WEIGHT * bm25_norm[i]
            item.score = round(TEXT_WEIGHT * text_score + SIGNAL_WEIGHT * item.score, 4)

        scored.sort(key=lambda x: x.score, reverse=True)

        # Step 5 — Enforce token budget per layer
        by_layer: dict[MemoryLayer, list[ScoredContextItem]] = {l: [] for l in MemoryLayer}
        for item in scored:
            by_layer[item.memory_layer].append(item)

        budget_trimmed: list[ScoredContextItem] = []
        for layer in MemoryLayer:
            trimmed = self._budget.enforce(by_layer[layer], layer)
            budget_trimmed.extend(trimmed)  # type: ignore[arg-type]

        budget_trimmed.sort(key=lambda x: x.score, reverse=True)

        # Step 6 — MMR diversification
        if use_mmr:
            has_embeddings = any(i.embedding for i in budget_trimmed)
            final_items = (
                apply_mmr(budget_trimmed, top_k, lambda_=MMR_LAMBDA)
                if has_embeddings
                else diversify_by_source(budget_trimmed, top_k)
            )
        else:
            final_items = budget_trimmed[:top_k]

        allocation: BudgetAllocation = self._budget.get_allocation()
        total_tokens = self._budget.estimate_tokens(final_items)

        return RetrieveResult(
            context_items=final_items,
            total_tokens=total_tokens,
            suggested_budget=allocation.to_dict(),
        )

    async def _track_access(self, item_ids: list[str]) -> None:
        """Fire-and-forget: update last_accessed_at on retrieved Qdrant points."""
        try:
            await self._qdrant.touch(item_ids)
        except Exception as exc:
            logger.debug("Access tracking failed (non-critical): %s", exc)

