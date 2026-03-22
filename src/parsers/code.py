from __future__ import annotations

import ast
import re
from pathlib import Path

from src.parsers.base import BaseParser, ParsedChunk

_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".go", ".rs", ".java", ".cpp", ".c",
    ".rb", ".php", ".swift", ".kt",
}

# Regex patterns for non-Python function/class boundaries
_FUNC_RE = re.compile(
    r"^(?:export\s+)?(?:async\s+)?(?:function|class|def|fn|func|pub fn|pub async fn)\s+(\w+)",
    re.MULTILINE,
)


class CodeParser(BaseParser):
    """
    Parse source code files into function/class level chunks.
    Uses Python ast for .py files; regex fallback for everything else.
    """

    @classmethod
    def supports(cls, source: str) -> bool:
        return Path(source).suffix.lower() in _CODE_EXTENSIONS

    async def parse(self, source: str) -> list[ParsedChunk]:
        path = Path(source)
        text = path.read_text(encoding="utf-8", errors="replace")
        lang = path.suffix.lower().lstrip(".")

        if path.suffix == ".py":
            return self._parse_python(text, source)
        return self._parse_generic(text, lang, source)

    @staticmethod
    def _parse_python(text: str, source: str) -> list[ParsedChunk]:
        chunks: list[ParsedChunk] = []
        try:
            tree = ast.parse(text)
        except SyntaxError:
            # Fall back to whole-file chunk
            return [ParsedChunk(content=text, title=Path(source).name,
                                metadata={"language": "python", "file": source})]

        lines = text.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not isinstance(node, ast.Module):
                    start = node.lineno - 1
                    end = node.end_lineno or (start + 1)
                    snippet = "\n".join(lines[start:end])
                    docstring = ast.get_docstring(node) or ""
                    chunks.append(ParsedChunk(
                        content=snippet,
                        title=node.name,
                        metadata={
                            "language": "python",
                            "file": source,
                            "name": node.name,
                            "docstring": docstring[:200],
                            "line_start": node.lineno,
                        },
                    ))
        # If nothing found (e.g. script with no defs), store as one chunk
        if not chunks:
            chunks.append(ParsedChunk(content=text, title=Path(source).name,
                                      metadata={"language": "python", "file": source}))
        return chunks

    @staticmethod
    def _parse_generic(text: str, lang: str, source: str) -> list[ParsedChunk]:
        chunks: list[ParsedChunk] = []
        matches = list(_FUNC_RE.finditer(text))
        if not matches:
            return [ParsedChunk(content=text, title=Path(source).name,
                                metadata={"language": lang, "file": source})]

        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            snippet = text[start:end].strip()
            name = match.group(1)
            chunks.append(ParsedChunk(
                content=snippet,
                title=name,
                metadata={"language": lang, "file": source, "name": name},
            ))
        return chunks

