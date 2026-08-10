from __future__ import annotations

from typing import Any

import pytest

from src.config import UsageSettings
from src.core.budget import estimate_tokens
from src.core.usage import UsageRecorder, usage_add_embedding, usage_begin, usage_end


def test_estimate_tokens_four_chars_per_token() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("x" * 4001) == 1000


def test_usage_context_accumulates_within_call() -> None:
    usage_begin()
    usage_add_embedding(10)
    usage_add_embedding(32)
    assert usage_end() == 42


def test_usage_end_resets_context() -> None:
    usage_begin()
    usage_add_embedding(5)
    usage_end()
    assert usage_end() == 0


def test_add_embedding_is_noop_outside_call_context() -> None:
    # background jobs (GC, consolidation) embed without an open call context
    usage_end()  # ensure no context is active
    usage_add_embedding(999)  # must not raise
    assert usage_end() == 0


class _FakePostgres:
    def __init__(self, fail: bool = False) -> None:
        self.rows: list[dict[str, Any]] = []
        self._fail = fail

    async def usage_add(self, **kwargs: Any) -> None:
        if self._fail:
            raise RuntimeError("db down")
        self.rows.append(kwargs)

    async def usage_totals(self, **kwargs: Any) -> dict[str, int]:
        return {
            "calls": len(self.rows),
            "input_tokens": sum(r["input_tokens"] for r in self.rows),
            "output_tokens": sum(r["output_tokens"] for r in self.rows),
            "embedding_tokens": sum(r["embedding_tokens"] for r in self.rows),
        }

    async def usage_stats(self, group_by: str = "tool", **kwargs: Any) -> list[dict[str, Any]]:
        return [{"tool": r["tool"], "calls": 1, **{k: r[k] for k in ("input_tokens", "output_tokens", "embedding_tokens")}} for r in self.rows]


@pytest.mark.asyncio
async def test_recorder_persists_row() -> None:
    postgres = _FakePostgres()
    recorder = UsageRecorder(postgres, UsageSettings(enabled=True))
    await recorder.record(
        user_id="u1", tool="context_store",
        input_tokens=10, output_tokens=20, embedding_tokens=30,
        project="synatyx",
    )
    assert postgres.rows == [{
        "user_id": "u1", "tool": "context_store",
        "input_tokens": 10, "output_tokens": 20, "embedding_tokens": 30,
        "project": "synatyx", "error": False,
    }]


@pytest.mark.asyncio
async def test_recorder_swallows_storage_errors() -> None:
    recorder = UsageRecorder(_FakePostgres(fail=True), UsageSettings(enabled=True))
    # must never propagate — a metering failure must not break the tool call
    await recorder.record(user_id="u1", tool="context_store", input_tokens=1, output_tokens=1)


@pytest.mark.asyncio
async def test_recorder_disabled_skips_write() -> None:
    postgres = _FakePostgres()
    recorder = UsageRecorder(postgres, UsageSettings(enabled=False))
    await recorder.record(user_id="u1", tool="context_store", input_tokens=1, output_tokens=1)
    assert postgres.rows == []


@pytest.mark.asyncio
async def test_stats_computes_embedding_cost() -> None:
    postgres = _FakePostgres()
    recorder = UsageRecorder(postgres, UsageSettings(enabled=True, embedding_price_per_mtok=0.02))
    await recorder.record(
        user_id="u1", tool="context_store",
        input_tokens=10, output_tokens=20, embedding_tokens=2_000_000,
    )
    stats = await recorder.stats(user_id="u1", days=7)
    assert stats["totals"]["embedding_tokens"] == 2_000_000
    assert stats["totals"]["embedding_cost_usd"] == pytest.approx(0.04)
    assert stats["days"] == 7
    assert stats["breakdown"][0]["tool"] == "context_store"
