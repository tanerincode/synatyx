from __future__ import annotations

from dataclasses import dataclass

from src.core.budget import BudgetAllocation, BudgetManager
from src.core.embedder import get_embedder
from src.core.score import score_items
from src.models.context import ContextItem, ScoredContextItem
from src.models.memory_layer import MemoryLayer
from src.storage.postgres import PostgresStorage
from src.storage.qdrant import QdrantStorage
from src.storage.redis import RedisStorage


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
        top_k: int = 10,
        memory_layers: list[MemoryLayer] | None = None,
    ) -> RetrieveResult:
        """
        Retrieve relevant context items for the given query.

        - Queries L1 (Redis), L2/L3/L4 (Qdrant)
        - Applies relevance scoring
        - Enforces token budget per layer
        - Returns sorted, deduplicated context items
        """
        layers = memory_layers or list(MemoryLayer)
        query_embedding = await self._embedder.embed(query)

        all_items: list[ContextItem] = []

        # L1 — working memory from Redis (always exact, no vector search)
        if MemoryLayer.L1 in layers and session_id:
            l1_items = await self._redis.l1_get(user_id, session_id)
            all_items.extend(l1_items)

        # L2, L3, L4 — vector similarity search from Qdrant
        vector_layers = [l for l in layers if l != MemoryLayer.L1]
        for layer in vector_layers:
            results = await self._qdrant.search(
                query_vector=query_embedding,
                user_id=user_id,
                top_k=top_k,
                memory_layer=layer,
            )
            all_items.extend(results)

        # Deduplicate by item id
        seen: set[str] = set()
        unique_items: list[ContextItem] = []
        for item in all_items:
            if item.id not in seen:
                seen.add(item.id)
                unique_items.append(item)

        # Score all items
        scored, _ = score_items(unique_items, query, query_embedding)

        # Enforce budget per layer
        by_layer: dict[MemoryLayer, list[ScoredContextItem]] = {l: [] for l in MemoryLayer}
        for item in scored:
            by_layer[item.memory_layer].append(item)

        final_items: list[ScoredContextItem] = []
        for layer in MemoryLayer:
            trimmed = self._budget.enforce(by_layer[layer], layer)
            final_items.extend(trimmed)  # type: ignore[arg-type]

        # Re-sort by score after budget enforcement
        final_items.sort(key=lambda x: x.score, reverse=True)

        # Limit to top_k
        final_items = final_items[:top_k]

        allocation: BudgetAllocation = self._budget.get_allocation()
        total_tokens = self._budget.estimate_tokens(final_items)

        return RetrieveResult(
            context_items=final_items,
            total_tokens=total_tokens,
            suggested_budget=allocation.to_dict(),
        )

