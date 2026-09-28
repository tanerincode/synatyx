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
TRACE_KEY_PREFIX = "synatyx:trace"


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
    # Session Activity Traces (server-side implicit capture)
    # -------------------------------------------------------------------------

    def _trace_key(self, user_id: str, scope: str) -> str:
        return f"{TRACE_KEY_PREFIX}:{user_id}:{scope}"

    async def trace_append(
        self,
        user_id: str,
        scope: str,
        event: dict[str, Any],
        max_events: int = 200,
        ttl_seconds: int = 172_800,
    ) -> None:
        """Append one activity event to the session trace buffer."""
        key = self._trace_key(user_id, scope)
        pipe = self._client.pipeline()
        pipe.rpush(key, json.dumps(event, default=str))
        pipe.ltrim(key, -max_events, -1)
        pipe.expire(key, ttl_seconds)
        await pipe.execute()

    async def trace_keys(self) -> list[tuple[str, str]]:
        """Return (user_id, scope) for every live trace buffer."""
        found: list[tuple[str, str]] = []
        async for key in self._client.scan_iter(match=f"{TRACE_KEY_PREFIX}:*"):
            parts = key.split(":", 3)  # synatyx : trace : user_id : scope
            if len(parts) == 4:
                found.append((parts[2], parts[3]))
        return found

    async def trace_last_ts(self, user_id: str, scope: str) -> str | None:
        """Timestamp of the newest event in a trace (None if buffer is gone)."""
        raw = await self._client.lindex(self._trace_key(user_id, scope), -1)
        if raw is None:
            return None
        try:
            ts = json.loads(raw).get("ts")
        except json.JSONDecodeError:
            return None
        return str(ts) if ts is not None else None

    async def trace_pop_all(self, user_id: str, scope: str) -> list[dict[str, Any]]:
        """Atomically drain a trace buffer (read + delete in one transaction)."""
        key = self._trace_key(user_id, scope)
        pipe = self._client.pipeline(transaction=True)
        pipe.lrange(key, 0, -1)
        pipe.delete(key)
        raw_items, _ = await pipe.execute()
        events: list[dict[str, Any]] = []
        for raw in raw_items:
            try:
                events.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return events

    # -------------------------------------------------------------------------
    # Generic TTL'd key/value (OAuth codes & tokens — see src/core/oauth.py)
    # -------------------------------------------------------------------------

    async def kv_set(self, key: str, value: str, ttl_seconds: int) -> None:
        """Store a string under an explicit key with a mandatory expiry."""
        await self._client.set(key, value, ex=ttl_seconds)

    async def kv_get(self, key: str) -> str | None:
        raw = await self._client.get(key)
        return str(raw) if raw is not None else None

    async def kv_delete(self, key: str) -> int:
        """Delete a key; returns the number of keys removed (0 or 1)."""
        return int(await self._client.delete(key))

    async def kv_incr(self, key: str, ttl_seconds: int) -> int:
        """Atomically increment a counter; the TTL is set when the key is created.

        INCR + EXPIRE run in one MULTI/EXEC block so a counter can never be
        left without an expiry (which would lock a client IP out forever).
        """
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, ttl_seconds, nx=True)
            count, _ = await pipe.execute()
        return int(count)

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def ping(self) -> bool:
        return await self._client.ping()

    async def close(self) -> None:
        await self._client.aclose()

