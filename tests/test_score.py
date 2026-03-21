import pytest
from datetime import datetime, timezone, timedelta
from src.core.score import (
    _recency_score,
    _cosine_similarity,
    _importance_score,
    _user_signal_score,
    score_item,
    score_items,
    DROP_THRESHOLD,
)
from src.models.context import ContextItem, ScoredContextItem
from src.models.memory_layer import MemoryLayer


def _make_item(
    content: str = "test content",
    importance: float = 0.5,
    is_pinned: bool = False,
    embedding: list[float] | None = None,
    semantic_score: float = 0.0,
    created_at: datetime | None = None,
) -> ContextItem:
    item = ContextItem(
        user_id="user-1",
        content=content,
        memory_layer=MemoryLayer.L3,
        importance=importance,
        is_pinned=is_pinned,
        embedding=embedding,
    )
    if created_at:
        item.created_at = created_at
    if semantic_score > 0:
        return ScoredContextItem(**item.model_dump(), semantic_score=semantic_score)
    return item


# ── Recency ───────────────────────────────────────────────────────────────────

def test_recency_score_just_created():
    score = _recency_score(datetime.now(timezone.utc))
    assert score > 0.9


def test_recency_score_one_day_ago():
    ts = datetime.now(timezone.utc) - timedelta(days=1)
    score = _recency_score(ts)
    assert 0.45 < score < 0.55  # ~0.5 after 24h half-life


def test_recency_score_old_item():
    ts = datetime.now(timezone.utc) - timedelta(days=30)
    score = _recency_score(ts)
    assert score < 0.05


# ── Cosine ────────────────────────────────────────────────────────────────────

def test_cosine_identical_vectors():
    v = [1.0, 0.0, 0.0]
    assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6


def test_cosine_orthogonal_vectors():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert _cosine_similarity(a, b) == 0.0


def test_cosine_empty_vectors():
    assert _cosine_similarity([], []) == 0.0
    assert _cosine_similarity([1.0], []) == 0.0


def test_cosine_different_lengths():
    assert _cosine_similarity([1.0, 0.0], [1.0]) == 0.0


# ── Importance ────────────────────────────────────────────────────────────────

def test_importance_score_normal():
    item = _make_item(importance=0.6)
    assert _importance_score(item) == 0.6


def test_importance_score_pinned_boost():
    item = _make_item(importance=0.6, is_pinned=True)
    assert _importance_score(item) == min(1.0, 0.6 + 0.2)


def test_importance_score_pinned_capped_at_one():
    item = _make_item(importance=0.95, is_pinned=True)
    assert _importance_score(item) == 1.0


# ── User signal ───────────────────────────────────────────────────────────────

def test_user_signal_full_overlap():
    item = _make_item(content="python programming language")
    score = _user_signal_score(item, "python programming language")
    assert score == 1.0


def test_user_signal_no_overlap():
    item = _make_item(content="cat dog bird")
    score = _user_signal_score(item, "python programming")
    assert score == 0.0


def test_user_signal_empty_query():
    item = _make_item(content="python programming")
    assert _user_signal_score(item, "") == 0.0


# ── score_item ────────────────────────────────────────────────────────────────

def test_score_item_uses_qdrant_semantic_score():
    """If semantic_score is already set (from Qdrant), should not recompute cosine."""
    item = _make_item(
        content="python code",
        semantic_score=0.85,
        embedding=[1.0, 0.0, 0.0],
    )
    scored = score_item(item, "python", query_embedding=[0.0, 1.0, 0.0])
    # Should use 0.85, not cosine([1,0,0],[0,1,0])=0.0
    assert scored.semantic_score == 0.85


def test_score_item_fallback_cosine_for_l1():
    """L1 items (no Qdrant score) should compute cosine locally."""
    item = _make_item(
        content="python code",
        embedding=[1.0, 0.0, 0.0],
        semantic_score=0.0,
    )
    scored = score_item(item, "query", query_embedding=[1.0, 0.0, 0.0])
    assert scored.semantic_score == 1.0


def test_score_item_returns_scored_context_item():
    item = _make_item()
    scored = score_item(item, "test")
    assert isinstance(scored, ScoredContextItem)
    assert 0.0 <= scored.score <= 1.0


# ── score_items ───────────────────────────────────────────────────────────────

def test_score_items_sorted_descending():
    items = [_make_item(importance=i / 10) for i in range(5)]
    kept, _ = score_items(items, "test")
    scores = [i.score for i in kept]
    assert scores == sorted(scores, reverse=True)


def test_score_items_drop_threshold():
    items = [
        _make_item(content="very relevant python code", importance=0.9),
        _make_item(content="x", importance=0.0),
    ]
    kept, dropped = score_items(items, "python")
    assert all(i.score >= DROP_THRESHOLD for i in kept)
    assert all(i.score < DROP_THRESHOLD for i in dropped)

