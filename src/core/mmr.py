from __future__ import annotations

import math

from src.models.context import ScoredContextItem

# Default MMR lambda: 0.5 = equal weight relevance vs diversity
# 1.0 = pure relevance (no diversification)
# 0.0 = pure diversity (no relevance)
DEFAULT_LAMBDA = 0.6


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two dense embedding vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def apply_mmr(
    items: list[ScoredContextItem],
    top_k: int,
    lambda_: float = DEFAULT_LAMBDA,
) -> list[ScoredContextItem]:
    """
    Apply Maximal Marginal Relevance to diversify retrieval results.
    Ported from CTX-EG internal/retrieve/mmr.go.

    Args:
        items:   Candidate items, sorted by score descending.
        top_k:   How many to select.
        lambda_: Trade-off between relevance (1.0) and diversity (0.0).

    Returns:
        Diversified list of up to top_k items.

    Algorithm:
        At each step, pick the candidate with the highest MMR score:
            MMR(i) = λ * relevance(i) - (1 - λ) * max_similarity(i, selected)
    """
    if not items:
        return []

    if top_k >= len(items):
        return items

    # Items without embeddings can't be diversified — keep them as-is at end
    with_emb = [i for i in items if i.embedding]
    without_emb = [i for i in items if not i.embedding]

    if not with_emb:
        return items[:top_k]

    selected: list[ScoredContextItem] = []
    candidates = list(with_emb)

    while len(selected) < top_k and candidates:
        best_idx = 0
        best_mmr = float("-inf")

        for i, candidate in enumerate(candidates):
            relevance = candidate.score

            # Max similarity to already-selected items
            max_sim = 0.0
            for sel in selected:
                sim = _cosine(candidate.embedding or [], sel.embedding or [])
                if sim > max_sim:
                    max_sim = sim

            mmr_score = lambda_ * relevance - (1.0 - lambda_) * max_sim

            if mmr_score > best_mmr:
                best_mmr = mmr_score
                best_idx = i

        selected.append(candidates.pop(best_idx))

    # Fill remaining slots with non-embedded items if needed
    remaining = top_k - len(selected)
    if remaining > 0:
        selected.extend(without_emb[:remaining])

    return selected


def diversify_by_source(
    items: list[ScoredContextItem],
    top_k: int,
) -> list[ScoredContextItem]:
    """
    Simple round-robin diversification by session_id (source proxy).
    Fallback when embeddings are not available.
    Ported from CTX-EG's diversifyByPosition.
    """
    if len(items) <= top_k:
        return items

    by_source: dict[str, list[ScoredContextItem]] = {}
    for item in items:
        key = item.session_id or item.memory_layer.value
        by_source.setdefault(key, []).append(item)

    keys = list(by_source.keys())
    result: list[ScoredContextItem] = []
    key_index = 0

    while len(result) < top_k:
        added = False
        for i in range(len(keys)):
            k = keys[(key_index + i) % len(keys)]
            if by_source[k]:
                result.append(by_source[k].pop(0))
                added = True
                if len(result) >= top_k:
                    break
        if not added:
            break
        key_index += 1

    return result

