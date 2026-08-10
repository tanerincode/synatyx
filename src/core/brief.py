from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from src.core.budget import estimate_tokens
from src.models.context import ContextItem
from src.models.memory_layer import MemoryLayer
from src.storage.postgres import PostgresStorage
from src.storage.qdrant import QdrantStorage

logger = logging.getLogger(__name__)

# Fraction of max_tokens allocated to each section. Knowledge gets the most —
# stable project facts are what the agent acts on; the rest is orientation.
SECTION_BUDGET: dict[str, float] = {
    "identity": 0.15,          # L4 — who the user is, how they work
    "last_session": 0.15,      # L2 — what happened recently
    "project_knowledge": 0.35, # L3 — checkpoints + stable facts
    "recent_changes": 0.15,    # anything new since the agent was last here
    "recent_attempts": 0.10,   # tried-and-failed records — don't repeat dead ends
    "open_tasks": 0.10,
}

# Items whose metadata.type matches these are internal records, not briefing
# material (skills are surfaced via context_skill_find, attempts get their own
# section).
_EXCLUDED_TYPES = ("skill",)
_ATTEMPT_TYPE = "attempt"

_SCAN_LIMIT = 500  # max items scrolled per collection when composing a brief


def _created_at(item: ContextItem) -> datetime:
    ts = item.created_at
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _dump(item: ContextItem, max_chars: int | None = None) -> dict[str, Any]:
    content = item.content
    truncated = False
    if max_chars is not None and len(content) > max_chars:
        content = content[:max_chars].rstrip() + "…"
        truncated = True
    dumped: dict[str, Any] = {
        "id": item.id,
        "content": content,
        "memory_layer": item.memory_layer.value,
        "importance": item.importance,
        "is_pinned": item.is_pinned,
        "origin": item.metadata.get("origin"),
        "created_at": _created_at(item).isoformat(),
    }
    if truncated:
        dumped["truncated"] = True
    if item.metadata.get("file_hashes"):
        from src.core.staleness import check_stale_files
        stale = check_stale_files(item.metadata)
        if stale:
            dumped["possibly_stale"] = True
            dumped["stale_files"] = stale
    return dumped


def _fit(items: list[ContextItem], budget_tokens: int) -> tuple[list[dict[str, Any]], int]:
    """Greedily pack items into a token budget, preserving order.

    If even the first item doesn't fit, it is included truncated — a section
    with content should never come back empty just because one item is long.
    """
    from src.core.budget import BudgetEntry, fit_entries

    entries = [
        BudgetEntry(
            tokens=item.token_estimate,
            payload=(lambda i=item: _dump(i)),
            raw_content=item.content,
        )
        for item in items
    ]
    result = fit_entries(entries, budget_tokens)
    return result.entries, result.used


class BriefService:
    """Compose a single token-budgeted session-start digest.

    Replaces the get_project → retrieve → task_list startup dance with one
    call: identity (L4), last session (L2), project knowledge (L3, pinned
    first), recent changes, recent failed attempts, open tasks, and
    collection stats.
    """

    def __init__(
        self,
        project_storage: QdrantStorage,
        l4_storage: QdrantStorage,
        postgres: PostgresStorage,
    ) -> None:
        self._project_storage = project_storage
        self._l4_storage = l4_storage
        self._postgres = postgres

    async def brief(
        self,
        user_id: str,
        project: str | None = None,
        session_id: str | None = None,
        max_tokens: int = 2000,
        recent_days: int = 7,
    ) -> dict[str, Any]:
        budgets = {k: int(max_tokens * v) for k, v in SECTION_BUDGET.items()}
        now = datetime.now(timezone.utc)
        recent_cutoff = now - timedelta(days=recent_days)

        l4_items = await self._l4_storage.list_items(
            user_id=user_id, memory_layer=MemoryLayer.L4, limit=_SCAN_LIMIT
        )
        project_items = await self._project_storage.list_items(
            user_id=user_id, project=project, limit=_SCAN_LIMIT
        )

        def item_type(item: ContextItem) -> str | None:
            return item.metadata.get("type")

        briefable = [i for i in project_items if item_type(i) not in _EXCLUDED_TYPES]
        attempts = [i for i in briefable if item_type(i) == _ATTEMPT_TYPE]
        facts = [i for i in briefable if item_type(i) != _ATTEMPT_TYPE]

        # Identity — user-global preferences, most important first
        l4_sorted = sorted(l4_items, key=lambda i: (i.importance, _created_at(i)), reverse=True)
        identity, identity_tokens = _fit(l4_sorted, budgets["identity"])

        # Last session — episodic L2, newest first
        l2_sorted = sorted(
            (i for i in facts if i.memory_layer == MemoryLayer.L2),
            key=_created_at, reverse=True,
        )
        last_session, l2_tokens = _fit(l2_sorted, budgets["last_session"])

        # Project knowledge — L3, pinned checkpoints first, then by importance
        l3_sorted = sorted(
            (i for i in facts if i.memory_layer == MemoryLayer.L3),
            key=lambda i: (i.is_pinned, i.importance, _created_at(i)), reverse=True,
        )
        knowledge, knowledge_tokens = _fit(l3_sorted, budgets["project_knowledge"])

        # Recent changes — anything new since the cutoff not already shown
        shown_ids = {d["id"] for d in identity + last_session + knowledge}
        recent_sorted = sorted(
            (i for i in facts if _created_at(i) >= recent_cutoff and i.id not in shown_ids),
            key=_created_at, reverse=True,
        )
        recent_changes, recent_tokens = _fit(recent_sorted, budgets["recent_changes"])

        # Recent attempts — tried-and-failed records, newest first
        attempts_sorted = sorted(attempts, key=_created_at, reverse=True)
        recent_attempts, attempt_tokens = _fit(attempts_sorted, budgets["recent_attempts"])

        open_tasks, task_tokens = await self._open_tasks(user_id, project, budgets["open_tasks"])
        stats = await self._stats(user_id, project)

        return {
            "identity": identity,
            "last_session": last_session,
            "project_knowledge": knowledge,
            "recent_changes": recent_changes,
            "recent_attempts": recent_attempts,
            "open_tasks": open_tasks,
            "stats": stats,
            "token_estimate": (
                identity_tokens + l2_tokens + knowledge_tokens
                + recent_tokens + attempt_tokens + task_tokens
            ),
            "max_tokens": max_tokens,
            "recent_days": recent_days,
        }

    async def _open_tasks(
        self, user_id: str, project: str | None, budget_tokens: int
    ) -> tuple[list[dict[str, Any]], int]:
        from src.models.task import TaskStatus

        try:
            pending = await self._postgres.task_list(
                user_id=user_id, status=TaskStatus.PENDING, project=project
            )
            in_progress = await self._postgres.task_list(
                user_id=user_id, status=TaskStatus.IN_PROGRESS, project=project
            )
        except Exception:
            logger.warning("Brief: task lookup failed — omitting open_tasks")
            return [], 0

        tasks: list[dict[str, Any]] = []
        used = 0
        for t in in_progress + pending:
            entry = {
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "description": (t.description or "")[:200],
            }
            tokens = estimate_tokens(t.title + entry["description"])
            if used + tokens > budget_tokens and tasks:
                break
            tasks.append(entry)
            used += tokens
        return tasks, used

    async def _stats(self, user_id: str, project: str | None) -> dict[str, Any]:
        try:
            by_layer: dict[str, int] = {}
            for layer in (MemoryLayer.L2, MemoryLayer.L3):
                by_layer[layer.value] = await self._project_storage.count_items(
                    user_id=user_id, memory_layer=layer, project=project
                )
            by_layer[MemoryLayer.L4.value] = await self._l4_storage.count_items(
                user_id=user_id, memory_layer=MemoryLayer.L4
            )
            return {"items_by_layer": by_layer, "total_items": sum(by_layer.values())}
        except Exception:
            logger.warning("Brief: stats lookup failed — omitting counts")
            return {"items_by_layer": {}, "total_items": None}
