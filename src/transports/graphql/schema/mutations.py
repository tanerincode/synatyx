from __future__ import annotations

from typing import Any, Optional

import strawberry
from strawberry.scalars import JSON
from strawberry.types import Info

from src.models.memory_layer import MemoryLayer
from src.transports.graphql.schema.types import StoreContextResult


@strawberry.type
class Mutation:
    @strawberry.mutation
    async def store_context(
        self,
        info: Info,
        content: str,
        user_id: str,
        memory_layer: str,
        importance: float = 0.5,
        session_id: Optional[str] = None,
        confidence: float = 1.0,
        metadata: Optional[JSON] = None,
    ) -> StoreContextResult:
        store_svc = info.context["store_service"]
        item_id, embedded = await store_svc.store(
            content=content,
            user_id=user_id,
            memory_layer=MemoryLayer(memory_layer),
            importance=importance,
            session_id=session_id,
            confidence=confidence,
            metadata=metadata,
        )
        return StoreContextResult(item_id=item_id, embedded=embedded)

    @strawberry.mutation
    async def delete_context(self, info: Info, item_id: str) -> bool:
        qdrant = info.context["qdrant"]
        await qdrant.delete(item_id)
        return True

    @strawberry.mutation
    async def delete_session(self, info: Info, session_id: str, user_id: str) -> bool:
        postgres = info.context["postgres"]
        redis = info.context["redis"]
        await postgres.session_delete(session_id)
        await redis.l1_clear(user_id, session_id)
        await redis.budget_reset(user_id, session_id)
        await postgres.audit(user_id, "delete_session", {"session_id": session_id})
        return True

    @strawberry.mutation
    async def trigger_summarize(
        self,
        info: Info,
        session_id: str,
        user_id: str,
        max_tokens: int = 500,
        focus: Optional[str] = None,
    ) -> bool:
        summarize_svc = info.context["summarize_service"]
        await summarize_svc.summarize_async(
            session_id=session_id,
            user_id=user_id,
            max_tokens=max_tokens,
            focus=focus,
        )
        return True

