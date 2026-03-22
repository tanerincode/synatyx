from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from src.models.context import ContextItem
from src.models.memory_layer import MemoryLayer

L1_MAX_MESSAGES = 20
L1_KEY_PREFIX = "synatyx:l1"
BUDGET_KEY_PREFIX = "synatyx:budget"
PUBSUB_CHANNEL = "synatyx:events"
PROJECT_KEY_PREFIX = "synatyx:project"


class RedisStorage:
    def __init__(self, url: str = "redis://localhost:6379") -> None:
        self._client: aioredis.Redis = aioredis.from_url(url, decode_responses=True)

    # -------------------------------------------------------------------------
    # L1 Sliding Window
    # -------------------------------------------------------------------------

    def _l1_key(self, user_id: str, session_id: str) -> str:
        return f"{L1_KEY_PREFIX}:{user_id}:{session_id}"

    async def l1_push(self, item: ContextItem) -> None:
        """Push a ContextItem to the L1 sliding window for a session."""
        key = self._l1_key(item.user_id, item.session_id or "default")
        payload = item.model_dump_json()
        await self._client.rpush(key, payload)
        # Trim to max window size, keeping pinned items safe via importance
        length = await self._client.llen(key)
        if length > L1_MAX_MESSAGES:
            await self._client.ltrim(key, length - L1_MAX_MESSAGES, -1)

    async def l1_get(self, user_id: str, session_id: str) -> list[ContextItem]:
        """Retrieve all items in the L1 window for a session."""
        key = self._l1_key(user_id, session_id)
        raw_items = await self._client.lrange(key, 0, -1)
        return [ContextItem.model_validate_json(r) for r in raw_items]

    async def l1_clear(self, user_id: str, session_id: str) -> None:
        """Clear the L1 window for a session (e.g. after summarization)."""
        key = self._l1_key(user_id, session_id)
        await self._client.delete(key)

    async def l1_length(self, user_id: str, session_id: str) -> int:
        key = self._l1_key(user_id, session_id)
        return await self._client.llen(key)

    # -------------------------------------------------------------------------
    # Token Budget Tracking
    # -------------------------------------------------------------------------

    def _budget_key(self, user_id: str, session_id: str) -> str:
        return f"{BUDGET_KEY_PREFIX}:{user_id}:{session_id}"

    async def budget_set(self, user_id: str, session_id: str, layer: MemoryLayer, tokens: int) -> None:
        key = self._budget_key(user_id, session_id)
        await self._client.hset(key, layer.value, tokens)

    async def budget_get(self, user_id: str, session_id: str) -> dict[str, int]:
        key = self._budget_key(user_id, session_id)
        raw = await self._client.hgetall(key)
        return {k: int(v) for k, v in raw.items()}

    async def budget_total(self, user_id: str, session_id: str) -> int:
        budget = await self.budget_get(user_id, session_id)
        return sum(budget.values())

    async def budget_reset(self, user_id: str, session_id: str) -> None:
        key = self._budget_key(user_id, session_id)
        await self._client.delete(key)

    # -------------------------------------------------------------------------
    # Pub/Sub Events
    # -------------------------------------------------------------------------

    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish an event to the Synatyx events channel."""
        message = json.dumps({"event": event_type, "payload": payload})
        await self._client.publish(PUBSUB_CHANNEL, message)

    async def subscribe(self) -> aioredis.client.PubSub:
        """Return a PubSub object subscribed to the Synatyx events channel."""
        pubsub = self._client.pubsub()
        await pubsub.subscribe(PUBSUB_CHANNEL)
        return pubsub

    # -------------------------------------------------------------------------
    # Project State
    # -------------------------------------------------------------------------

    async def project_set(self, user_id: str, slug: str) -> None:
        """Persist the active project slug for a user."""
        key = f"{PROJECT_KEY_PREFIX}:{user_id}"
        await self._client.set(key, slug)

    async def project_get(self, user_id: str) -> str | None:
        """Return the active project slug for a user, or None if not set."""
        key = f"{PROJECT_KEY_PREFIX}:{user_id}"
        return await self._client.get(key)

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def ping(self) -> bool:
        return await self._client.ping()

    async def close(self) -> None:
        await self._client.aclose()

