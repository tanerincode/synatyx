from __future__ import annotations

from src.parsers.base import BaseParser, ParsedChunk


class DocxParser(BaseParser):
    """Parse .docx files into section-level chunks."""

    @classmethod
    def supports(cls, source: str) -> bool:
        return source.lower().endswith(".docx")

    async def parse(self, source: str) -> list[ParsedChunk]:
        import docx

        doc = docx.Document(source)
        blocks = self._extract_blocks(doc)
        return self._chunk_by_section(blocks)

    @staticmethod
    def _extract_blocks(doc) -> list[dict]:
        blocks = []
        for child in doc.element.body:
            tag = child.tag.split("}")[-1]
            if tag == "p":
                import docx.text.paragraph
                para = docx.text.paragraph.Paragraph(child, doc)
                text = para.text.strip()
                if text:
                    style_name = para.style.name if para.style else ""
                    blocks.append({"type": "para", "text": text, "style": style_name})
            elif tag == "tbl":
                from docx.table import Table
                table = Table(child, doc)
                rows = []
                for row in table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        rows.append(" | ".join(cells))
                if rows:
                    blocks.append({"type": "table", "text": "\n".join(rows)})
        return blocks

    @staticmethod
    def _chunk_by_section(blocks: list[dict]) -> list[ParsedChunk]:
        chunks: list[ParsedChunk] = []
        current_title = "Introduction"
        current_parts: list[str] = []

        def _flush():
            if current_parts:
                chunks.append(ParsedChunk(
                    content="\n".join(current_parts),
                    title=current_title,
                    metadata={"section": current_title},
                ))

        for b in blocks:
            text = b["text"]
            style = b.get("style", "")
            is_heading = (
                "Heading" in style
                or (len(text) < 80 and text[:2].rstrip(". ").isdigit())
                or (len(text) < 80 and text[0].isdigit() and "." in text[:4])
            )
            if is_heading:
                _flush()
                current_title = text
                current_parts = []
            else:
                current_parts.append(text)

        _flush()
        return [c for c in chunks if not c.is_empty]

