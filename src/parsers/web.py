from __future__ import annotations

import re

from src.parsers.base import BaseParser, ParsedChunk

_BLANK = re.compile(r"\n{3,}")
_REMOVE_TAGS = {"script", "style", "nav", "footer", "header", "aside", "form"}


class WebParser(BaseParser):
    """Fetch a URL and extract readable text chunks using BeautifulSoup."""

    @classmethod
    def supports(cls, source: str) -> bool:
        return source.startswith(("http://", "https://"))

    async def parse(self, source: str) -> list[ParsedChunk]:
        import httpx
        from bs4 import BeautifulSoup

        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
            resp = await client.get(source, headers={"User-Agent": "Synatyx/1.0"})
            resp.raise_for_status()
            html = resp.text

        soup = BeautifulSoup(html, "lxml")

        # Remove noise tags
        for tag in soup.find_all(_REMOVE_TAGS):
            tag.decompose()

        # Try to find main content area
        main = (
            soup.find("main")
            or soup.find("article")
            or soup.find(id=re.compile(r"content|main|body", re.I))
            or soup.body
        )

        page_title = soup.title.string.strip() if soup.title else source

        chunks: list[ParsedChunk] = []
        current_title = page_title
        current_parts: list[str] = []

        def _flush():
            text = "\n".join(current_parts).strip()
            text = _BLANK.sub("\n\n", text)
            if text:
                chunks.append(ParsedChunk(
                    content=text,
                    title=current_title,
                    metadata={"url": source, "section": current_title},
                ))

        if main:
            for el in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "code"]):
                tag = el.name
                text = el.get_text(" ", strip=True)
                if not text:
                    continue
                if tag in ("h1", "h2", "h3", "h4"):
                    _flush()
                    current_title = text
                    current_parts = []
                else:
                    current_parts.append(text)
            _flush()

        if not chunks:
            # Fallback: grab all text
            raw = (main or soup).get_text("\n", strip=True)
            chunks.append(ParsedChunk(content=raw[:8000], title=page_title,
                                      metadata={"url": source}))
        return chunks

