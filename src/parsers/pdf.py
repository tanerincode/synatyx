from __future__ import annotations

import re

from src.parsers.base import BaseParser, ParsedChunk

_BLANK_LINES = re.compile(r"\n{3,}")


class PdfParser(BaseParser):
    """Parse PDF files using pdfplumber — one chunk per page group."""

    @classmethod
    def supports(cls, source: str) -> bool:
        return source.lower().endswith(".pdf")

    async def parse(self, source: str) -> list[ParsedChunk]:
        import pdfplumber

        chunks: list[ParsedChunk] = []
        with pdfplumber.open(source) as pdf:
            total = len(pdf.pages)
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                text = _BLANK_LINES.sub("\n\n", text).strip()
                if not text:
                    continue
                # Split long pages into paragraphs
                paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
                # Group up to 3 paragraphs per chunk to keep token size reasonable
                for j in range(0, len(paragraphs), 3):
                    group = "\n\n".join(paragraphs[j: j + 3])
                    chunks.append(ParsedChunk(
                        content=group,
                        title=f"Page {i + 1}",
                        metadata={"page": i + 1, "total_pages": total},
                    ))
        return chunks

