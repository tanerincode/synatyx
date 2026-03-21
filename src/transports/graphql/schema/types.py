from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import strawberry
from strawberry.scalars import JSON


@strawberry.enum
class MemoryLayerGQL:
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"


@strawberry.type
class ContextItemGQL:
    id: str
    user_id: str
    session_id: Optional[str]
    content: str
    memory_layer: str
    importance: float
    score: float
    is_pinned: bool
    is_deprecated: bool
    created_at: datetime
    metadata: JSON


@strawberry.type
class KeyEntityGQL:
    name: str
    type: str
    value: str
    confidence: float


@strawberry.type
class SessionGQL:
    session_id: str
    user_id: str
    status: str
    summary: Optional[str]
    key_entities: list[KeyEntityGQL]
    token_count: int
    message_count: int
    created_at: datetime
    updated_at: datetime


@strawberry.type
class BudgetAllocationGQL:
    system_prompt: int
    current_message: int
    response_headroom: int
    l1_working: int
    l2_episodic: int
    l3_semantic: int
    l4_procedural: int
    total_available: int
    total_used: int
    remaining: int


@strawberry.type
class RetrieveContextResult:
    context_items: list[ContextItemGQL]
    total_tokens: int
    suggested_budget: BudgetAllocationGQL


@strawberry.type
class StoreContextResult:
    item_id: str
    embedded: bool


@strawberry.type
class UserStatsGQL:
    user_id: str
    total_items: int
    total_sessions: int
    l1_count: int
    l2_count: int
    l3_count: int
    l4_count: int


@strawberry.type
class ContextUpdatedEvent:
    item_id: str
    user_id: str
    memory_layer: str
    embedded: bool


@strawberry.type
class SessionSummarizedEvent:
    session_id: str
    user_id: str
    tokens_saved: int


@strawberry.type
class BudgetAlertEvent:
    user_id: str
    session_id: str
    layer: str
    used_tokens: int
    limit_tokens: int

