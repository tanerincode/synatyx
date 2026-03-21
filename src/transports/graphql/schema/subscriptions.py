from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator

import strawberry
from strawberry.types import Info

from src.transports.graphql.schema.types import (
    BudgetAlertEvent,
    ContextUpdatedEvent,
    SessionSummarizedEvent,
)

logger = logging.getLogger(__name__)

PUBSUB_CHANNEL = "synatyx:events"


async def _event_stream(
    redis_storage,
    event_type: str,
) -> AsyncGenerator[dict, None]:
    """Subscribe to Redis Pub/Sub and yield events matching event_type."""
    pubsub = await redis_storage.subscribe()
    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    if data.get("event") == event_type:
                        yield data["payload"]
                except (json.JSONDecodeError, KeyError):
                    continue
            else:
                await asyncio.sleep(0.1)
    finally:
        await pubsub.unsubscribe(PUBSUB_CHANNEL)
        await pubsub.close()


@strawberry.type
class Subscription:
    @strawberry.subscription
    async def context_updated(
        self,
        info: Info,
        user_id: str,
    ) -> AsyncGenerator[ContextUpdatedEvent, None]:
        """
        Fires whenever a new context item is stored for the given user.
        Triggered by context_store via Redis Pub/Sub.
        """
        redis = info.context["redis"]
        async for payload in _event_stream(redis, "context_stored"):
            if payload.get("user_id") == user_id:
                yield ContextUpdatedEvent(
                    item_id=payload["item_id"],
                    user_id=payload["user_id"],
                    memory_layer=payload["memory_layer"],
                    embedded=payload["embedded"],
                )

    @strawberry.subscription
    async def session_summarized(
        self,
        info: Info,
        user_id: str,
    ) -> AsyncGenerator[SessionSummarizedEvent, None]:
        """
        Fires whenever a session summarization completes for the given user.
        Triggered by summarize_async via Redis Pub/Sub.
        """
        redis = info.context["redis"]
        async for payload in _event_stream(redis, "session_summarized"):
            if payload.get("user_id") == user_id:
                yield SessionSummarizedEvent(
                    session_id=payload["session_id"],
                    user_id=payload["user_id"],
                    tokens_saved=payload.get("tokens_saved", 0),
                )

    @strawberry.subscription
    async def budget_alert(
        self,
        info: Info,
        user_id: str,
    ) -> AsyncGenerator[BudgetAlertEvent, None]:
        """
        Fires whenever a token budget limit is approached for the given user.
        Triggered by budget enforcement in retrieve/store via Redis Pub/Sub.
        """
        redis = info.context["redis"]
        async for payload in _event_stream(redis, "budget_alert"):
            if payload.get("user_id") == user_id:
                yield BudgetAlertEvent(
                    user_id=payload["user_id"],
                    session_id=payload["session_id"],
                    layer=payload["layer"],
                    used_tokens=payload["used_tokens"],
                    limit_tokens=payload["limit_tokens"],
                )

