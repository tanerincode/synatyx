from __future__ import annotations

from typing import Optional

import strawberry
from strawberry.types import Info

from src.models.memory_layer import MemoryLayer
from src.transports.graphql.schema.types import (
    BudgetAllocationGQL,
    ContextItemGQL,
    KeyEntityGQL,
    RetrieveContextResult,
    SessionGQL,
    UserStatsGQL,
)


def _map_context_item(item) -> ContextItemGQL:
    return ContextItemGQL(
        id=item.id,
        user_id=item.user_id,
        session_id=item.session_id,
        content=item.content,
        memory_layer=item.memory_layer.value,
        importance=item.importance,
        score=item.score,
        is_pinned=item.is_pinned,
        is_deprecated=item.is_deprecated,
        created_at=item.created_at,
        metadata=item.metadata,
    )


def _map_session(session) -> SessionGQL:
    return SessionGQL(
        session_id=session.session_id,
        user_id=session.user_id,
        status=session.status.value,
        summary=session.summary,
        key_entities=[
            KeyEntityGQL(name=e.name, type=e.type, value=e.value, confidence=e.confidence)
            for e in session.key_entities
        ],
        token_count=session.token_count,
        message_count=session.message_count,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@strawberry.type
class Query:
    @strawberry.field
    async def retrieve_context(
        self,
        info: Info,
        query: str,
        user_id: str,
        session_id: Optional[str] = None,
        top_k: int = 10,
        memory_layers: Optional[list[str]] = None,
    ) -> RetrieveContextResult:
        retrieve_svc = info.context["retrieve_service"]
        layers = [MemoryLayer(l) for l in memory_layers] if memory_layers else None
        result = await retrieve_svc.retrieve(
            query=query,
            user_id=user_id,
            session_id=session_id,
            top_k=top_k,
            memory_layers=layers,
        )
        budget = result.suggested_budget
        return RetrieveContextResult(
            context_items=[_map_context_item(i) for i in result.context_items],
            total_tokens=result.total_tokens,
            suggested_budget=BudgetAllocationGQL(
                system_prompt=budget["system_prompt"],
                current_message=budget["current_message"],
                response_headroom=budget["response_headroom"],
                l1_working=budget["L1"],
                l2_episodic=budget["L2"],
                l3_semantic=budget["L3"],
                l4_procedural=budget["L4"],
                total_available=budget["total_available"],
                total_used=budget["total_used"],
                remaining=budget["remaining"],
            ),
        )

    @strawberry.field
    async def get_session(self, info: Info, session_id: str) -> Optional[SessionGQL]:
        postgres = info.context["postgres"]
        session = await postgres.session_get(session_id)
        return _map_session(session) if session else None

    @strawberry.field
    async def get_user_stats(self, info: Info, user_id: str) -> UserStatsGQL:
        # Basic stats — counts per layer from Qdrant
        qdrant = info.context["qdrant"]
        postgres = info.context["postgres"]

        # Count Qdrant vectors per layer
        counts = {l: 0 for l in MemoryLayer}
        for layer in [MemoryLayer.L2, MemoryLayer.L3, MemoryLayer.L4]:
            results = await qdrant.search(
                query_vector=[0.0] * 1536,
                user_id=user_id,
                top_k=1000,
                memory_layer=layer,
                score_threshold=0.0,
            )
            counts[layer] = len(results)

        return UserStatsGQL(
            user_id=user_id,
            total_items=sum(counts.values()),
            total_sessions=0,  # extend with postgres query as needed
            l1_count=counts[MemoryLayer.L1],
            l2_count=counts[MemoryLayer.L2],
            l3_count=counts[MemoryLayer.L3],
            l4_count=counts[MemoryLayer.L4],
        )

