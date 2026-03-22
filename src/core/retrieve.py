from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

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

# Score fusion weights (from CTX-EG: 0.6 dense + 0.4 BM25)
DENSE_WEIGHT = 0.6
BM25_WEIGHT = 0.4

# MMR lambda: 0.6 = slightly favour relevance over diversity
MMR_LAMBDA = 0.6


@dataclass
class RetrieveResult:
    context_items: list[ScoredContextItem]
    total_tokens: int
    suggested_budget: dict[str, int]


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

    async def retrieve(
        self,
        query: str,
        user_id: str,
        session_id: str | None = None,
        project: str | None = None,
        top_k: int = 10,
        memory_layers: list[MemoryLayer] | None = None,
        use_mmr: bool = True,
    ) -> RetrieveResult:
        """
        Hybrid retrieval: dense kNN + BM25 score fusion + MMR diversification.

        Pipeline (from CTX-EG + Synatyx combined):
        1. L1 from Redis (exact, no vector search)
        2. L2/L3/L4 from Qdrant (dense kNN — fetch 3x top_k as candidates)
        3. BM25 re-score candidates against query
        4. Fuse: final_score = 0.6 * dense + 0.4 * BM25
        5. Apply recency / importance / user signal scoring
        6. Enforce token budget per layer
        7. Apply MMR diversification
        """
        layers = memory_layers or list(MemoryLayer)
        query_embedding = await self._embedder.embed(query)

        all_items: list[ContextItem] = []

        # Step 1 — L1 working memory from Redis
        if MemoryLayer.L1 in layers and session_id:
            l1_items = await self._redis.l1_get(user_id, session_id)
            all_items.extend(l1_items)

        # Step 2 — Fetch 3x candidates from Qdrant for better BM25 + MMR pool
        candidate_k = top_k * 3
        vector_layers = [l for l in layers if l != MemoryLayer.L1]
        for layer in vector_layers:
            results = await self._qdrant.search(
                query_vector=query_embedding,
                user_id=user_id,
                top_k=candidate_k,
                memory_layer=layer,
                session_id=session_id,
                project=project,
            )
            all_items.extend(results)
            # Fire-and-forget access tracking — update last_accessed_at in Qdrant
            if results:
                hit_ids = [r.id for r in results]
                asyncio.create_task(self._track_access(hit_ids))

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
            # Fuse dense score (already in item.semantic_score) with BM25
            fused = DENSE_WEIGHT * item.semantic_score + BM25_WEIGHT * bm25_norm[i]
            # Blend with full relevance score (recency, importance, user signal)
            item.score = round(DENSE_WEIGHT * fused + (1 - DENSE_WEIGHT) * item.score, 4)

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

