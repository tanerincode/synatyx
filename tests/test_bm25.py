import pytest
from src.core.bm25 import (
    BM25Index,
    bm25_score,
    build_sparse_vector,
    document_frequency,
    sparse_cosine_similarity,
    term_frequency,
    tokenize,
)


def test_tokenize_basic():
    tokens = tokenize("The quick brown fox")
    assert "quick" in tokens
    assert "brown" in tokens
    assert "fox" in tokens


def test_tokenize_removes_stop_words():
    tokens = tokenize("the and is of a an")
    assert tokens == []


def test_tokenize_lowercases():
    tokens = tokenize("Hello World")
    assert "hello" in tokens
    assert "world" in tokens


def test_tokenize_removes_short_tokens():
    tokens = tokenize("I a go")
    # "I" and "a" are stop words; "go" length > 1 so it stays
    assert "go" in tokens


def test_tokenize_unicode_normalization():
    tokens = tokenize("café naïve résumé")
    assert len(tokens) > 0


def test_term_frequency():
    tf = term_frequency(["dog", "cat", "dog", "bird"])
    assert tf["dog"] == 2
    assert tf["cat"] == 1
    assert tf["bird"] == 1


def test_document_frequency():
    corpus = [["cat", "dog"], ["cat", "fish"], ["bird"]]
    df = document_frequency(corpus)
    assert df["cat"] == 2
    assert df["dog"] == 1
    assert df["fish"] == 1
    assert df["bird"] == 1


def test_bm25_score_zero_for_no_match():
    query = tokenize("python programming")
    doc = tokenize("the weather is sunny today")
    score = bm25_score(query, doc, avg_doc_len=5.0, total_docs=10, df={})
    assert score == 0.0


def test_bm25_score_higher_for_relevant_doc():
    query = tokenize("python programming language")
    relevant = tokenize("python is great programming language for developers")
    irrelevant = tokenize("the weather today is sunny and warm outside")
    df = document_frequency([relevant, irrelevant])
    avg_len = (len(relevant) + len(irrelevant)) / 2

    score_rel = bm25_score(query, relevant, avg_len, 2, df)
    score_irr = bm25_score(query, irrelevant, avg_len, 2, df)
    assert score_rel > score_irr


def test_bm25_score_empty_doc():
    score = bm25_score(["python"], [], avg_doc_len=5.0, total_docs=1, df={})
    assert score == 0.0


def test_build_sparse_vector_keys_are_terms():
    tokens = tokenize("machine learning model training")
    df = document_frequency([tokens])
    vec = build_sparse_vector(tokens, avg_doc_len=4.0, total_docs=1, df=df)
    assert all(isinstance(k, str) for k in vec)
    assert all(isinstance(v, float) for v in vec.values())


def test_sparse_cosine_similarity_identical():
    vec = {"python": 0.5, "code": 0.3}
    sim = sparse_cosine_similarity(vec, vec)
    assert abs(sim - 1.0) < 1e-6


def test_sparse_cosine_similarity_no_overlap():
    vec1 = {"python": 0.5}
    vec2 = {"java": 0.5}
    assert sparse_cosine_similarity(vec1, vec2) == 0.0


def test_sparse_cosine_similarity_empty():
    assert sparse_cosine_similarity({}, {"python": 0.5}) == 0.0


def test_bm25_index_score_all_length():
    docs = ["cat dog bird", "fish whale shark", "python code class"]
    index = BM25Index(docs)
    scores = index.score_all("cat python")
    assert len(scores) == len(docs)


def test_bm25_index_relevant_doc_scores_highest():
    docs = [
        "python programming language code",
        "sunny weather today warm outside",
        "java programming language code",
    ]
    index = BM25Index(docs)
    scores = index.score_all("python programming")
    assert scores[0] >= scores[1]  # python doc > weather doc

