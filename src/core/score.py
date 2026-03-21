from __future__ import annotations

import math
from datetime import datetime, timezone

from src.models.context import ContextItem, ScoredContextItem


# Weights for combining score factors (must sum to 1.0)
WEIGHT_RECENCY = 0.25
WEIGHT_SEMANTIC = 0.40
WEIGHT_IMPORTANCE = 0.25
WEIGHT_USER_SIGNAL = 0.10

# Threshold below which items are considered droppable
DROP_THRESHOLD = 0.20


def _recency_score(created_at: datetime) -> float:
    """
    Recency score: 1.0 / (1 + seconds_since_creation)
    Normalized to [0, 1] using a 24h half-life.
    """
    now = datetime.now(timezone.utc)
    seconds = max(0.0, (now - created_at).total_seconds())
    return 1.0 / (1.0 + seconds / 86_400)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """
    Fallback cosine similarity — only used for L1 items that bypass Qdrant.
    For all other layers Qdrant returns the native cosine score directly.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _importance_score(item: ContextItem) -> float:
    base = item.importance
    if item.is_pinned:
        base = min(1.0, base + 0.2)
    return base


def _user_signal_score(item: ContextItem, query: str) -> float:
    if not query:
        return 0.0
    query_words = set(query.lower().split())
    content_words = set(item.content.lower().split())
    overlap = query_words & content_words
    if not overlap:
        return 0.0
    return min(1.0, len(overlap) / max(len(query_words), 1))


def score_item(
    item: ContextItem,
    query: str,
    query_embedding: list[float] | None = None,
) -> ScoredContextItem:
    """
    Score a single ContextItem against the current query.

    Semantic score source:
    - L2/L3/L4 items: use Qdrant's native cosine score (already on semantic_score)
    - L1 items (Redis, no Qdrant score): compute cosine locally as fallback
    """
    recency = _recency_score(item.created_at)
    importance = _importance_score(item)
    user_signal = _user_signal_score(item, query)

    # Use Qdrant's native cosine score if already available, else compute locally
    qdrant_score = item.semantic_score if isinstance(item, ScoredContextItem) else 0.0
    semantic = qdrant_score if qdrant_score > 0.0 else _cosine_similarity(
        item.embedding or [], query_embedding or []
    )

    combined = (
        WEIGHT_RECENCY * recency
        + WEIGHT_SEMANTIC * semantic
        + WEIGHT_IMPORTANCE * importance
        + WEIGHT_USER_SIGNAL * user_signal
    )

    _scored_fields = {"recency_score", "semantic_score", "importance_score", "user_signal_score", "score"}
    base = item.model_dump(exclude=_scored_fields)
    return ScoredContextItem(
        **base,
        recency_score=round(recency, 4),
        semantic_score=round(semantic, 4),
        importance_score=round(importance, 4),
        user_signal_score=round(user_signal, 4),
        score=round(combined, 4),
    )


def score_items(
    items: list[ContextItem],
    query: str,
    query_embedding: list[float] | None = None,
) -> tuple[list[ScoredContextItem], list[ScoredContextItem]]:
    """
    Score a list of ContextItems and split into kept and dropped.

    Returns:
        scored_items: sorted by score descending, above DROP_THRESHOLD
        dropped_items: below DROP_THRESHOLD — safe to discard
    """
    scored = [score_item(item, query, query_embedding) for item in items]
    scored.sort(key=lambda x: x.score, reverse=True)

    kept = [i for i in scored if i.score >= DROP_THRESHOLD]
    dropped = [i for i in scored if i.score < DROP_THRESHOLD]

    return kept, dropped

