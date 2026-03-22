from __future__ import annotations

import re

from src.parsers.base import BaseParser, ParsedChunk

_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")


class MarkdownParser(BaseParser):
    """Parse .md / .mdx files — chunk by heading."""

    @classmethod
    def supports(cls, source: str) -> bool:
        return source.lower().endswith((".md", ".mdx", ".markdown"))

    async def parse(self, source: str) -> list[ParsedChunk]:
        with open(source, encoding="utf-8") as f:
            text = f.read()
        return self._chunk(text)

    @staticmethod
    def _chunk(text: str) -> list[ParsedChunk]:
        chunks: list[ParsedChunk] = []
        current_title = "Introduction"
        current_lines: list[str] = []

        def _flush():
            content = "\n".join(current_lines).strip()
            if content:
                chunks.append(ParsedChunk(
                    content=content,
                    title=current_title,
                    metadata={"section": current_title},
                ))

        for line in text.splitlines():
            m = _HEADING.match(line)
            if m:
                _flush()
                current_title = m.group(2).strip()
                current_lines = []
            else:
                current_lines.append(line)

        _flush()
        return chunks

