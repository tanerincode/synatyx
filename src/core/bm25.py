from __future__ import annotations

import math
import unicodedata
import re

# BM25 hyperparameters (from CTX-EG defaults)
K1 = 1.5   # Term frequency saturation
B = 0.75   # Length normalization

STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "he", "in", "is", "it", "its", "of", "on", "that", "the",
    "to", "was", "will", "with", "this", "they", "have", "had", "but",
    "not", "what", "all", "been", "when", "who", "which", "or", "we",
    "do", "did", "if", "so", "up", "out", "about", "into", "than",
    "then", "she", "her", "him", "his", "your", "our", "their",
})


def tokenize(text: str) -> list[str]:
    """Lowercase, unicode-normalize, split on non-alphanumeric, remove stop words."""
    text = unicodedata.normalize("NFKC", text.lower())
    tokens = re.split(r"[^a-z0-9]+", text)
    return [t for t in tokens if len(t) > 1 and t not in STOP_WORDS]


def term_frequency(tokens: list[str]) -> dict[str, int]:
    tf: dict[str, int] = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1
    return tf


def document_frequency(corpus: list[list[str]]) -> dict[str, int]:
    df: dict[str, int] = {}
    for doc in corpus:
        for term in set(doc):
            df[term] = df.get(term, 0) + 1
    return df


def _idf(term: str, total_docs: int, df: dict[str, int]) -> float:
    freq = df.get(term, 0)
    return math.log((total_docs - freq + 0.5) / (freq + 0.5) + 1.0)


def bm25_score(
    query_tokens: list[str],
    doc_tokens: list[str],
    avg_doc_len: float,
    total_docs: int,
    df: dict[str, int],
) -> float:
    """
    BM25 relevance score for a single document.
    """
    if not doc_tokens:
        return 0.0

    doc_len = len(doc_tokens)
    tf = term_frequency(doc_tokens)
    score = 0.0

    for term in query_tokens:
        freq = tf.get(term, 0)
        if freq == 0:
            continue
        idf = _idf(term, total_docs, df)
        numerator = freq * (K1 + 1.0)
        denominator = freq + K1 * (1.0 - B + B * (doc_len / avg_doc_len))
        score += idf * (numerator / denominator)

    return score


def build_sparse_vector(
    tokens: list[str],
    avg_doc_len: float,
    total_docs: int,
    df: dict[str, int],
) -> dict[str, float]:
    """
    Build a sparse BM25 weight vector for a document.
    Used for score fusion with dense vectors.
    """
    if not tokens:
        return {}

    doc_len = len(tokens)
    tf = term_frequency(tokens)
    vec: dict[str, float] = {}

    for term, freq in tf.items():
        idf = _idf(term, total_docs, df)
        numerator = freq * (K1 + 1.0)
        denominator = freq + K1 * (1.0 - B + B * (doc_len / avg_doc_len))
        vec[term] = idf * (numerator / denominator)

    return vec


def sparse_cosine_similarity(vec1: dict[str, float], vec2: dict[str, float]) -> float:
    """Cosine similarity between two sparse BM25 vectors (shared terms only)."""
    if not vec1 or not vec2:
        return 0.0
    dot = sum(vec1[t] * vec2[t] for t in vec1 if t in vec2)
    mag1 = math.sqrt(sum(v * v for v in vec1.values()))
    mag2 = math.sqrt(sum(v * v for v in vec2.values()))
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot / (mag1 * mag2)


class BM25Index:
    """
    In-memory BM25 index for a batch of documents.
    Used during retrieval to score candidate items without a search engine.
    """

    def __init__(self, corpus: list[str]) -> None:
        self._tokenized = [tokenize(doc) for doc in corpus]
        self._df = document_frequency(self._tokenized)
        self._total = len(corpus)
        self._avg_len = (
            sum(len(t) for t in self._tokenized) / max(self._total, 1)
        )

    def score(self, query: str, doc_index: int) -> float:
        query_tokens = tokenize(query)
        return bm25_score(
            query_tokens,
            self._tokenized[doc_index],
            self._avg_len,
            self._total,
            self._df,
        )

    def score_all(self, query: str) -> list[float]:
        query_tokens = tokenize(query)
        return [
            bm25_score(query_tokens, doc_tokens, self._avg_len, self._total, self._df)
            for doc_tokens in self._tokenized
        ]

