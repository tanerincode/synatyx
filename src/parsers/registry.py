from __future__ import annotations

from src.parsers.base import BaseParser
from src.parsers.code import CodeParser
from src.parsers.docx import DocxParser
from src.parsers.markdown import MarkdownParser
from src.parsers.pdf import PdfParser
from src.parsers.web import WebParser

# Order matters — more specific parsers first
_PARSERS: list[type[BaseParser]] = [
    DocxParser,
    PdfParser,
    MarkdownParser,
    CodeParser,
    WebParser,
]


def get_parser(source: str) -> BaseParser:
    """Return the appropriate parser for the given file path or URL."""
    for cls in _PARSERS:
        if cls.supports(source):
            return cls()
    raise ValueError(
        f"No parser available for {source!r}. "
        f"Supported: .docx, .pdf, .md, .mdx, code files, http(s):// URLs"
    )


def supported_extensions() -> list[str]:
    """Return a human-readable list of all supported source types."""
    return [
        ".docx", ".pdf",
        ".md / .mdx / .markdown",
        ".py / .js / .ts / .tsx / .jsx / .go / .rs / .java / .cpp / .rb",
        "http:// and https:// URLs",
    ]

