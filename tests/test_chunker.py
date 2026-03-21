import pytest
from src.core.chunker import RecursiveChunker, Chunk, DEFAULT_CHUNK_SIZE, DEFAULT_CHUNK_OVERLAP


@pytest.fixture
def chunker():
    return RecursiveChunker(chunk_size=100, chunk_overlap=20)


def test_empty_input(chunker):
    assert chunker.chunk("") == []
    assert chunker.chunk("   ") == []


def test_short_content_no_split(chunker):
    text = "This is a short sentence."
    chunks = chunker.chunk(text)
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].start_pos == 0
    assert chunks[0].end_pos == len(text)


def test_long_content_splits(chunker):
    text = ("word " * 40).strip()  # ~200 chars, well above chunk_size=100
    chunks = chunker.chunk(text)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.text) <= 100 + 20  # allow slight overflow at word boundary


def test_chunk_indices_assigned(chunker):
    text = ("paragraph one. " * 10) + "\n\n" + ("paragraph two. " * 10)
    chunks = chunker.chunk(text)
    for i, c in enumerate(chunks):
        assert c.index == i


def test_overlap_content_continuity(chunker):
    """Adjacent chunks should share some content via overlap."""
    text = "The quick brown fox jumps over the lazy dog. " * 5
    chunks = chunker.chunk(text)
    if len(chunks) > 1:
        for i in range(len(chunks) - 1):
            tail = chunks[i].text[-chunker.chunk_overlap:]
            head = chunks[i + 1].text[:chunker.chunk_overlap]
            # Overlap means tail words appear at the start of next chunk
            assert any(word in chunks[i + 1].text for word in tail.split()[:3])


def test_paragraph_separator_preferred():
    """Should split on double newlines before falling back to spaces."""
    chunker = RecursiveChunker(chunk_size=50, chunk_overlap=10)
    text = "First paragraph content here.\n\nSecond paragraph content here."
    chunks = chunker.chunk(text)
    assert len(chunks) >= 2
    assert "First paragraph" in chunks[0].text


def test_chunk_text_returns_strings(chunker):
    text = "Hello world. " * 20
    texts = chunker.chunk_text(text)
    assert all(isinstance(t, str) for t in texts)
    assert len(texts) > 0


def test_default_chunker_sizes():
    chunker = RecursiveChunker()
    assert chunker.chunk_size == DEFAULT_CHUNK_SIZE
    assert chunker.chunk_overlap == DEFAULT_CHUNK_OVERLAP


def test_token_estimate():
    chunk = Chunk(text="a" * 400, start_pos=0, end_pos=400)
    assert chunk.token_estimate == 100  # 400 // 4


def test_positions_non_overlapping_start_end(chunker):
    text = "sentence one. " * 15
    chunks = chunker.chunk(text)
    for c in chunks:
        assert c.start_pos >= 0
        assert c.end_pos > c.start_pos

