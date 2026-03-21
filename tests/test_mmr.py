import pytest
from src.core.mmr import apply_mmr, diversify_by_source
from src.models.context import ScoredContextItem
from src.models.memory_layer import MemoryLayer


def _make_item(
    content: str,
    score: float,
    embedding: list[float] | None = None,
    session_id: str | None = None,
    layer: MemoryLayer = MemoryLayer.L3,
) -> ScoredContextItem:
    return ScoredContextItem(
        user_id="test-user",
        content=content,
        memory_layer=layer,
        importance=0.5,
        score=score,
        semantic_score=score,
        embedding=embedding,
        session_id=session_id,
    )


def _unit_vec(dim: int, hot: int) -> list[float]:
    v = [0.0] * dim
    v[hot % dim] = 1.0
    return v


def test_empty_input():
    assert apply_mmr([], top_k=5) == []


def test_fewer_items_than_top_k():
    items = [_make_item(f"item {i}", 0.9 - i * 0.1, _unit_vec(4, i)) for i in range(3)]
    result = apply_mmr(items, top_k=10)
    assert len(result) == 3


def test_returns_top_k_items():
    items = [_make_item(f"item {i}", 0.9 - i * 0.05, _unit_vec(8, i)) for i in range(10)]
    result = apply_mmr(items, top_k=5)
    assert len(result) == 5


def test_lambda_1_pure_relevance():
    """Lambda=1.0 should select items in pure relevance order."""
    items = [
        _make_item("item A", 0.9, _unit_vec(4, 0)),
        _make_item("item B", 0.8, _unit_vec(4, 0)),  # similar to A
        _make_item("item C", 0.7, _unit_vec(4, 0)),
    ]
    result = apply_mmr(items, top_k=2, lambda_=1.0)
    assert result[0].content == "item A"
    assert result[1].content == "item B"


def test_lambda_0_pure_diversity():
    """Lambda=0.0 should prefer diverse items over high relevance."""
    # Two items pointing in same direction (similar), one orthogonal (diverse)
    items = [
        _make_item("item A", 0.9, [1.0, 0.0, 0.0, 0.0]),
        _make_item("item B", 0.85, [1.0, 0.0, 0.0, 0.0]),  # similar to A
        _make_item("item C", 0.5, [0.0, 1.0, 0.0, 0.0]),   # diverse
    ]
    result = apply_mmr(items, top_k=2, lambda_=0.0)
    contents = [r.content for r in result]
    # After selecting A, C should be chosen over B due to diversity
    assert "item A" in contents
    assert "item C" in contents


def test_no_embeddings_fallback():
    """Items without embeddings go to without_emb pool, returned after MMR items."""
    items = [_make_item(f"item {i}", 0.9 - i * 0.1) for i in range(5)]
    result = apply_mmr(items, top_k=3)
    assert len(result) == 3


def test_diversify_by_source_round_robin():
    items = [
        _make_item("s1-1", 0.9, session_id="s1"),
        _make_item("s1-2", 0.8, session_id="s1"),
        _make_item("s2-1", 0.7, session_id="s2"),
        _make_item("s2-2", 0.6, session_id="s2"),
    ]
    result = diversify_by_source(items, top_k=2)
    assert len(result) == 2
    sessions = {r.session_id for r in result}
    assert len(sessions) == 2  # one from each source


def test_diversify_by_source_fewer_than_top_k():
    items = [_make_item(f"item {i}", 0.9) for i in range(2)]
    result = diversify_by_source(items, top_k=10)
    assert len(result) == 2

