from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from src.config import TrackingSettings
from src.core.tracking import SessionTracker, build_trace_event, render_trace
from src.models.memory_layer import MemoryLayer


class FakeRedisTrace:
    def __init__(self) -> None:
        self.buffers: dict[tuple[str, str], list[dict[str, Any]]] = {}

    async def trace_append(
        self, user_id: str, scope: str, event: dict[str, Any],
        max_events: int = 200, ttl_seconds: int = 0,
    ) -> None:
        buf = self.buffers.setdefault((user_id, scope), [])
        buf.append(event)
        del buf[:-max_events]

    async def trace_keys(self) -> list[tuple[str, str]]:
        return list(self.buffers.keys())

    async def trace_last_ts(self, user_id: str, scope: str) -> str | None:
        buf = self.buffers.get((user_id, scope))
        return buf[-1].get("ts") if buf else None

    async def trace_pop_all(self, user_id: str, scope: str) -> list[dict[str, Any]]:
        return self.buffers.pop((user_id, scope), [])


class FakeStore:
    def __init__(self) -> None:
        self.stored: list[dict[str, Any]] = []

    async def store(self, **kwargs: Any):
        self.stored.append(kwargs)
        return ["id-1"], True


def _ts(minutes_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()


def _tracker(**overrides: Any) -> tuple[SessionTracker, FakeRedisTrace]:
    redis = FakeRedisTrace()
    tracker = SessionTracker(redis, TrackingSettings(**overrides))  # type: ignore[arg-type]
    return tracker, redis


# ── event builder ────────────────────────────────────────────────────────────

def test_retrieve_and_store_events() -> None:
    e = build_trace_event(
        "context_retrieve", {"query": "auth flow"}, {"context_items": [1, 2]}
    )
    assert e == {"op": "retrieve", "query": "auth flow", "matched": 2}

    e = build_trace_event("context_store", {"content": "fact"}, {"item_id": "x"})
    assert e == {"op": "store", "count": 1, "previews": ["fact"]}

    e = build_trace_event(
        "context_store",
        {"items": [{"content": "a"}, {"content": "b"}]},
        {"stored": 2},
    )
    assert e is not None and e["count"] == 2 and e["previews"] == ["a", "b"]


def test_untracked_and_error_calls_ignored() -> None:
    assert build_trace_event("context_get_project", {}, {"project": "x"}) is None
    assert build_trace_event("context_list", {}, {"items": []}) is None
    assert build_trace_event("context_store", {"content": "x"}, {"error": "boom"}) is None


def test_long_values_truncated() -> None:
    e = build_trace_event("context_retrieve", {"query": "q" * 500}, {"context_items": []})
    assert e is not None and len(e["query"]) == 120


# ── render ───────────────────────────────────────────────────────────────────

def test_render_trace_sections() -> None:
    events = [
        {"op": "brief", "ts": _ts(60)},
        {"op": "retrieve", "query": "auth flow", "ts": _ts(55)},
        {"op": "retrieve", "query": "auth flow", "ts": _ts(50)},  # dup — dedup'd
        {"op": "store", "count": 1, "previews": ["JWT secret lives in env"], "ts": _ts(40)},
        {"op": "checkpoint", "name": "auth-shipped", "ts": _ts(35)},
        {"op": "task_add", "title": "rotate keys", "ts": _ts(31)},
    ]
    text = render_trace("my-api", events)
    assert text.startswith("[Session trace: my-api")
    assert "6 memory ops" in text
    assert text.count("auth flow") == 1
    assert "JWT secret lives in env" in text
    assert "auth-shipped" in text
    assert 'added "rotate keys"' in text
    assert "retrieve×2" in text


# ── record ───────────────────────────────────────────────────────────────────

async def test_record_appends_with_timestamp_and_scope() -> None:
    tracker, redis = _tracker()
    await tracker.record(
        "u1", "context_retrieve", {"query": "x", "session_id": "proj"}, {"context_items": []}
    )
    events = redis.buffers[("u1", "proj")]
    assert len(events) == 1 and "ts" in events[0]


async def test_record_disabled_or_untracked_is_noop() -> None:
    tracker, redis = _tracker(enabled=False)
    await tracker.record("u1", "context_retrieve", {"query": "x"}, {"context_items": []})
    assert redis.buffers == {}

    tracker, redis = _tracker()
    await tracker.record("u1", "context_get_project", {}, {"project": "p"})
    await tracker.record("", "context_retrieve", {"query": "x"}, {"context_items": []})
    assert redis.buffers == {}


# ── compaction ───────────────────────────────────────────────────────────────

async def test_idle_trace_compacted_to_l2() -> None:
    tracker, redis = _tracker(idle_minutes=30, min_events=3)
    redis.buffers[("u1", "proj")] = [
        {"op": "retrieve", "query": "q1", "ts": _ts(120)},
        {"op": "store", "count": 1, "previews": ["fact"], "ts": _ts(100)},
        {"op": "retrieve", "query": "q2", "ts": _ts(90)},
    ]
    store = FakeStore()

    async def get_store(user_id: str) -> FakeStore:
        return store

    compacted = await tracker.compact_idle(get_store)
    assert compacted == 1
    assert redis.buffers == {}  # drained
    saved = store.stored[0]
    assert saved["memory_layer"] == MemoryLayer.L2
    assert saved["session_id"] == "proj"
    assert saved["metadata"]["type"] == "session-trace"
    assert saved["origin"] == "agent-inferred"
    assert "[Session trace: proj" in saved["content"]


async def test_active_trace_left_alone() -> None:
    tracker, redis = _tracker(idle_minutes=30)
    redis.buffers[("u1", "proj")] = [
        {"op": "retrieve", "query": "q", "ts": _ts(5)},  # 5 min ago — active
    ] * 4
    store = FakeStore()

    async def get_store(user_id: str) -> FakeStore:
        return store

    assert await tracker.compact_idle(get_store) == 0
    assert ("u1", "proj") in redis.buffers
    assert store.stored == []


async def test_tiny_idle_trace_dropped_as_noise() -> None:
    tracker, redis = _tracker(idle_minutes=30, min_events=3)
    redis.buffers[("u1", "proj")] = [
        {"op": "retrieve", "query": "q", "ts": _ts(90)},
        {"op": "retrieve", "query": "q2", "ts": _ts(80)},
    ]
    store = FakeStore()

    async def get_store(user_id: str) -> FakeStore:
        return store

    assert await tracker.compact_idle(get_store) == 0
    assert redis.buffers == {}  # drained but not stored
    assert store.stored == []


async def test_store_failure_never_raises() -> None:
    tracker, redis = _tracker(idle_minutes=30, min_events=1)
    redis.buffers[("u1", "proj")] = [
        {"op": "retrieve", "query": "q", "ts": _ts(90)},
    ]

    async def broken_store(user_id: str):
        raise RuntimeError("qdrant down")

    assert await tracker.compact_idle(broken_store) == 0


# ── new context-engine ops ───────────────────────────────────────────────────

def test_event_pack():
    e = build_trace_event(
        "context_pack",
        {"query": "how does budgeting work"},
        {"token_estimate": 1234},
    )
    assert e == {"op": "pack", "query": "how does budgeting work", "tokens": 1234}


def test_event_index():
    e = build_trace_event(
        "context_index",
        {"source": "/repo/src"},
        {"files_indexed": 12},
    )
    assert e == {"op": "index", "source": "/repo/src", "files": 12}


def test_event_index_search():
    e = build_trace_event(
        "context_index_search",
        {"query": "SectionBudgeter"},
        {"count": 3},
    )
    assert e == {"op": "index_search", "query": "SectionBudgeter", "matched": 3}


def test_render_trace_counts_pack_as_topic():
    events = [
        {"op": "pack", "query": "retrieval budgeting", "ts": "2026-08-03T10:00:00+00:00"},
    ]
    rendered = render_trace("synatyx", events)
    assert "retrieval budgeting" in rendered
