from __future__ import annotations

from dataclasses import dataclass, field

# Default chunk settings (from CTX-EG: 400–800 tokens, 10–15% overlap)
DEFAULT_CHUNK_SIZE = 600   # characters (~150 tokens @ 4 chars/token)
DEFAULT_CHUNK_OVERLAP = 80  # ~13% overlap

SEPARATORS = [
    "\n\n",  # Paragraphs
    "\n",    # Lines
    ". ",    # Sentences
    "! ",
    "? ",
    "; ",    # Clauses
    ", ",    # Phrases
    " ",     # Words
    "",      # Characters (last resort)
]


@dataclass
class Chunk:
    text: str
    start_pos: int
    end_pos: int
    index: int = 0
    metadata: dict = field(default_factory=dict)

    @property
    def token_estimate(self) -> int:
        return len(self.text) // 4


class RecursiveChunker:
    """
    Strategy: Try to split on natural boundaries (paragraphs → sentences → words).
    Falls back to character split only as last resort.
    Applies overlap to preserve context across chunk boundaries.
    """

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        separators: list[str] | None = None,
    ) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or SEPARATORS

    def chunk(self, text: str) -> list[Chunk]:
        if not text.strip():
            return []
        raw = self._split(text, self.separators, 0)
        for i, c in enumerate(raw):
            c.index = i
        return raw

    def chunk_text(self, text: str) -> list[str]:
        return [c.text for c in self.chunk(text)]

    def _split(self, text: str, separators: list[str], start_pos: int) -> list[Chunk]:
        if len(text) <= self.chunk_size:
            return [Chunk(text=text, start_pos=start_pos, end_pos=start_pos + len(text))]

        for i, sep in enumerate(separators):
            if sep == "":
                return self._char_split(text, start_pos)

            if sep in text:
                parts = text.split(sep)
                return self._merge(parts, sep, separators[i:], start_pos)

        return self._char_split(text, start_pos)

    def _merge(
        self, parts: list[str], sep: str, separators: list[str], start_pos: int
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        current = ""
        current_start = start_pos

        for i, part in enumerate(parts):
            candidate = (current + sep + part) if current else part

            if len(candidate) <= self.chunk_size:
                current = candidate
            else:
                if current:
                    chunks.append(Chunk(
                        text=current,
                        start_pos=current_start,
                        end_pos=current_start + len(current),
                    ))
                    overlap = self._get_overlap(current)
                    current = (overlap + sep + part) if overlap else part
                    current_start = current_start + len(current) - len(overlap)
                else:
                    if len(separators) > 1:
                        sub = self._split(part, separators[1:], current_start)
                        chunks.extend(sub)
                        current_start = sub[-1].end_pos if sub else current_start
                    else:
                        current = part

            if i < len(parts) - 1:
                current_start += len(sep)

        if current:
            chunks.append(Chunk(
                text=current,
                start_pos=current_start,
                end_pos=current_start + len(current),
            ))

        return chunks

    def _get_overlap(self, text: str) -> str:
        if len(text) <= self.chunk_overlap:
            return text
        return text[len(text) - self.chunk_overlap:]

    def _char_split(self, text: str, start_pos: int) -> list[Chunk]:
        chunks: list[Chunk] = []
        step = max(1, self.chunk_size - self.chunk_overlap)
        i = 0
        while i < len(text):
            end = min(i + self.chunk_size, len(text))
            chunks.append(Chunk(
                text=text[i:end],
                start_pos=start_pos + i,
                end_pos=start_pos + end,
            ))
            if end >= len(text):
                break
            i += step
        return chunks


# Default singleton
default_chunker = RecursiveChunker()

