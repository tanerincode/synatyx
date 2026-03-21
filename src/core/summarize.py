from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.models.memory_layer import MemoryLayer
from src.models.session import KeyEntity
from src.storage.postgres import PostgresStorage
from src.storage.redis import RedisStorage

if TYPE_CHECKING:
    from src.core.store import StoreService

logger = logging.getLogger(__name__)

SUMMARIZE_PROMPT = """You are a memory summarizer. Given the following conversation messages, produce:
1. A concise summary (max {max_tokens} tokens)
2. A JSON list of key entities in format: [{{"name": "", "type": "", "value": "", "confidence": 0.9}}]

Entity types: project, language, preference, decision, fact, person

Messages:
{messages}

Respond in this exact format:
SUMMARY: <summary text>
ENTITIES: <json array>"""


@dataclass
class SummarizeResult:
    summary: str
    key_entities: list[KeyEntity]
    tokens_saved: int


class SummarizeService:
    def __init__(
        self,
        redis: RedisStorage,
        postgres: PostgresStorage,
        store: "StoreService | None" = None,
    ) -> None:
        self._redis = redis
        self._postgres = postgres
        self._store = store

    async def summarize(
        self,
        session_id: str,
        user_id: str,
        max_tokens: int = 500,
        focus: str | None = None,
    ) -> SummarizeResult:
        """
        Summarize the L1 working memory for a session using an LLM.
        Async and off the critical path — call with asyncio.create_task().

        Steps:
        1. Fetch L1 items from Redis
        2. Call LLM to produce summary + key entities
        3. Store summary as L2 item
        4. Clear L1 window for this session
        5. Update session record in Postgres
        """
        l1_items = await self._redis.l1_get(user_id, session_id)
        if not l1_items:
            return SummarizeResult(summary="", key_entities=[], tokens_saved=0)

        tokens_before = sum(i.token_estimate for i in l1_items)

        messages_text = "\n".join(
            f"[{i.created_at.strftime('%H:%M')}] {i.content}" for i in l1_items
        )

        prompt = SUMMARIZE_PROMPT.format(
            max_tokens=max_tokens,
            messages=messages_text + (f"\n\nFocus on: {focus}" if focus else ""),
        )

        summary, key_entities = await self._call_llm(prompt)
        tokens_after = len(summary) // 4  # rough estimate

        # Store summary as L2 episodic memory vector in Qdrant
        if summary and self._store:
            try:
                await self._store.store(
                    content=summary,
                    user_id=user_id,
                    memory_layer=MemoryLayer.L2,
                    importance=0.8,
                    session_id=session_id,
                    metadata={"source": "summarize", "session_id": session_id},
                )
            except Exception:
                logger.warning("Failed to embed and store L2 summary for session %s", session_id)

        # Publish summarization event
        await self._redis.publish("session_summarized", {
            "session_id": session_id,
            "user_id": user_id,
            "tokens_saved": tokens_before - tokens_after,
        })

        # Update session in postgres
        session = await self._postgres.session_get(session_id)
        if session:
            session.mark_summarized(summary, key_entities)
            await self._postgres.session_update(session)

        # Clear the L1 window — summary replaces it
        await self._redis.l1_clear(user_id, session_id)

        await self._postgres.audit(user_id, "context_summarize", {
            "session_id": session_id,
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
        })

        return SummarizeResult(
            summary=summary,
            key_entities=key_entities,
            tokens_saved=max(0, tokens_before - tokens_after),
        )

    async def summarize_async(
        self,
        session_id: str,
        user_id: str,
        max_tokens: int = 500,
        focus: str | None = None,
    ) -> None:
        """Fire-and-forget wrapper — call this from the critical path."""
        asyncio.create_task(
            self._safe_summarize(session_id, user_id, max_tokens, focus)
        )

    async def _safe_summarize(self, session_id: str, user_id: str, max_tokens: int, focus: str | None) -> None:
        try:
            await self.summarize(session_id, user_id, max_tokens, focus)
        except Exception:
            logger.exception("Background summarization failed for session %s", session_id)

    async def _call_llm(self, prompt: str) -> tuple[str, list[KeyEntity]]:
        """Call the configured LLM to produce summary and entities."""
        from src.config import settings
        import json

        if settings.embedding.provider == "openai" and settings.embedding.openai_api_key:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=settings.embedding.openai_api_key)
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600,
                temperature=0.3,
            )
            raw = response.choices[0].message.content or ""
        else:
            # Fallback: basic extractive summary (no LLM available)
            lines = prompt.split("\n")
            raw = f"SUMMARY: {' '.join(lines[:3])}\nENTITIES: []"

        summary = ""
        key_entities: list[KeyEntity] = []

        for line in raw.splitlines():
            if line.startswith("SUMMARY:"):
                summary = line.removeprefix("SUMMARY:").strip()
            elif line.startswith("ENTITIES:"):
                try:
                    entities_raw = json.loads(line.removeprefix("ENTITIES:").strip())
                    key_entities = [KeyEntity(**e) for e in entities_raw]
                except Exception:
                    key_entities = []

        return summary, key_entities

