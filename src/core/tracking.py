from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from src.config import TrackingSettings
from src.models.memory_layer import MemoryLayer
from src.storage.redis import RedisStorage

logger = logging.getLogger(__name__)

_PREVIEW = 120


def build_trace_event(
    tool: str, args: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any] | None:
    """Distill one MCP tool call into a compact trace event, or None if the
    call isn't session-narrative material (project lookups, lists, errors)."""
    if not isinstance(result, dict) or "error" in result:
        return None

    if tool == "context_retrieve":
        return {
            "op": "retrieve",
            "query": str(args.get("query", ""))[:_PREVIEW],
            "matched": len(result.get("context_items", [])),
        }
    if tool == "context_store":
        if args.get("items"):
            previews = [
                str(i.get("content", ""))[:_PREVIEW] for i in args["items"][:3]
            ]
            return {"op": "store", "count": len(args["items"]), "previews": previews}
        return {
            "op": "store",
            "count": 1,
            "previews": [str(args.get("content", ""))[:_PREVIEW]],
        }
    if tool == "context_checkpoint":
        return {"op": "checkpoint", "name": str(args.get("name", ""))[:_PREVIEW]}
    if tool == "context_ingest":
        return {
            "op": "ingest",
            "source": str(args.get("source", ""))[:_PREVIEW],
            "chunks": result.get("chunks_stored", 0),
        }
    if tool == "context_task_add":
        return {"op": "task_add", "title": str(args.get("title", ""))[:_PREVIEW]}
    if tool == "context_task_update":
        return {"op": "task_update", "status": args.get("status")}
    if tool == "context_deprecate":
        return {"op": "deprecate"}
    if tool == "context_brief":
        return {"op": "brief"}
    if tool == "context_pack":
        return {
            "op": "pack",
            "query": str(args.get("query", ""))[:_PREVIEW],
            "tokens": result.get("token_estimate", 0),
        }
    if tool == "context_index":
        return {
            "op": "index",
            "source": str(args.get("source", ""))[:_PREVIEW],
            "files": result.get("files_indexed", 0),
        }
    if tool == "context_index_search":
        return {
            "op": "index_search",
            "query": str(args.get("query", ""))[:_PREVIEW],
            "matched": result.get("count", 0),
        }
    return None


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw)
        return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    except ValueError:
        return None


def _dedup(values: list[str], cap: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        key = v.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(v)
        if len(out) >= cap:
            break
    return out


def render_trace(scope: str, events: list[dict[str, Any]]) -> str:
    """Deterministic session digest from raw trace events — no LLM needed."""
    first_ts = _parse_ts(events[0].get("ts"))
    last_ts = _parse_ts(events[-1].get("ts"))
    window = ""
    if first_ts and last_ts:
        window = f", {first_ts.strftime('%Y-%m-%d %H:%M')}–{last_ts.strftime('%H:%M')} UTC"

    queries: list[str] = []
    stored: list[str] = []
    checkpoints: list[str] = []
    tasks: list[str] = []
    ingests: list[str] = []
    counts: dict[str, int] = {}

    for e in events:
        op = e.get("op", "?")
        counts[op] = counts.get(op, 0) + 1
        if op in ("retrieve", "pack", "index_search") and e.get("query"):
            queries.append(e["query"])
        elif op == "store":
            stored.extend(e.get("previews", []))
        elif op == "checkpoint" and e.get("name"):
            checkpoints.append(e["name"])
        elif op == "task_add" and e.get("title"):
            tasks.append(f'added "{e["title"]}"')
        elif op == "task_update" and e.get("status"):
            tasks.append(f"marked a task {e['status']}")
        elif op == "ingest" and e.get("source"):
            ingests.append(e["source"])

    lines = [f"[Session trace: {scope}{window}, {len(events)} memory ops]"]
    if queries:
        lines.append("Topics explored: " + "; ".join(_dedup(queries, 5)))
    if stored:
        lines.append("Facts stored: " + "; ".join(_dedup(stored, 5)))
    if checkpoints:
        lines.append("Checkpoints: " + "; ".join(_dedup(checkpoints, 3)))
    if ingests:
        lines.append("Ingested: " + "; ".join(_dedup(ingests, 3)))
    if tasks:
        lines.append("Tasks: " + "; ".join(_dedup(tasks, 5)))
    lines.append(
        "Activity: " + ", ".join(f"{op}×{n}" for op, n in sorted(counts.items()))
    )
    return "\n".join(lines)


class SessionTracker:
    """Implicit episodic capture from MCP traffic the server already sees.

    record() appends a compact event per tool call; compact_idle() turns each
    trace whose session went quiet into one L2 memory. No client setup, no
    hooks — a session can no longer end without leaving a trace.
    """

    def __init__(self, redis: RedisStorage, settings: TrackingSettings) -> None:
        self._redis = redis
        self._settings = settings

    async def record(
        self, user_id: str, tool: str, args: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Never raises — tracking must not interfere with the tool call."""
        if not self._settings.enabled or not user_id:
            return
        try:
            event = build_trace_event(tool, args, result)
            if event is None:
                return
            event["ts"] = datetime.now(UTC).isoformat()
            scope = args.get("session_id") or args.get("project") or "default"
            await self._redis.trace_append(
                user_id,
                str(scope),
                event,
                max_events=self._settings.max_events,
                ttl_seconds=self._settings.trace_ttl_hours * 3600,
            )
        except Exception:
            logger.debug("Trace record failed (non-critical)", exc_info=True)

    async def compact_idle(
        self, get_store: Callable[[str], Awaitable[Any]]
    ) -> int:
        """Compact every idle trace into an L2 memory. Returns count stored.

        get_store(user_id) must return a StoreService bound to that user's
        active project collection.
        """
        if not self._settings.enabled:
            return 0

        idle_cutoff = datetime.now(UTC) - timedelta(
            minutes=self._settings.idle_minutes
        )
        compacted = 0

        for user_id, scope in await self._redis.trace_keys():
            last_ts = _parse_ts(await self._redis.trace_last_ts(user_id, scope))
            if last_ts is None or last_ts > idle_cutoff:
                continue  # buffer vanished or session still active

            events = await self._redis.trace_pop_all(user_id, scope)
            if len(events) < self._settings.min_events:
                continue  # noise — drained and dropped

            try:
                store = await get_store(user_id)
                await store.store(
                    content=render_trace(scope, events),
                    user_id=user_id,
                    memory_layer=MemoryLayer.L2,
                    importance=0.5,
                    session_id=scope if scope != "default" else None,
                    metadata={
                        "type": "session-trace",
                        "source": "activity-tracker",
                        "ops": len(events),
                    },
                    origin="agent-inferred",
                )
                compacted += 1
            except Exception:
                logger.exception(
                    "Trace compaction failed for %s/%s — events dropped", user_id, scope
                )

        if compacted:
            logger.info("Session tracking: compacted %d idle trace(s) into L2", compacted)
        return compacted
