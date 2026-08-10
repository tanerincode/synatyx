from __future__ import annotations

import logging
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config import UsageSettings
from src.storage.postgres import PostgresStorage

logger = logging.getLogger(__name__)

# Embedding tokens consumed by the in-flight tool call. The dispatch layer
# opens a context with usage_begin(); the embedder adds to it wherever it runs
# in the call tree. Outside a call context (GC daemon, consolidation, watch
# loops) the var is None and usage_add_embedding is a no-op.
_embedding_tokens: ContextVar[list[int] | None] = ContextVar("embedding_tokens", default=None)


def usage_begin() -> None:
    _embedding_tokens.set([0])


def usage_add_embedding(tokens: int) -> None:
    bucket = _embedding_tokens.get()
    if bucket is not None:
        bucket[0] += tokens


def usage_end() -> int:
    bucket = _embedding_tokens.get()
    _embedding_tokens.set(None)
    return bucket[0] if bucket else 0


class UsageRecorder:
    """Persists one tool_usage row per call and aggregates spend for reads.

    record() must never break a tool call — storage errors are logged and
    swallowed, mirroring SessionTracker.record.
    """

    def __init__(self, postgres: PostgresStorage, config: UsageSettings) -> None:
        self._postgres = postgres
        self._config = config

    async def record(
        self,
        user_id: str,
        tool: str,
        input_tokens: int,
        output_tokens: int,
        embedding_tokens: int = 0,
        project: str | None = None,
        error: bool = False,
    ) -> None:
        if not self._config.enabled:
            return
        try:
            await self._postgres.usage_add(
                user_id=user_id,
                tool=tool,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                embedding_tokens=embedding_tokens,
                project=project,
                error=error,
            )
        except Exception:
            logger.warning("usage recording failed for tool %r", tool, exc_info=True)

    async def stats(
        self,
        user_id: str | None = None,
        project: str | None = None,
        days: int = 30,
        group_by: str = "tool",
    ) -> dict[str, Any]:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        totals = await self._postgres.usage_totals(user_id=user_id, project=project, since=since)
        breakdown = await self._postgres.usage_stats(
            user_id=user_id, project=project, since=since, group_by=group_by,
        )
        totals["embedding_cost_usd"] = round(
            totals["embedding_tokens"] / 1_000_000 * self._config.embedding_price_per_mtok, 6
        )
        return {
            "totals": totals,
            "breakdown": breakdown,
            "days": days,
            "group_by": group_by,
        }
