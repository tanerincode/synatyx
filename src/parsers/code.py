from __future__ import annotations

import ast
import re
from pathlib import Path

from src.parsers.base import BaseParser, ParsedChunk
from src.parsers.dart import DartParseError, parse_dart

_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".go", ".rs", ".java", ".cpp", ".c",
    ".rb", ".php", ".swift", ".kt", ".dart",
    ".yml", ".yaml", ".toml", ".json",
}

# Generated Dart: noise in the index, rebuilt by build_runner.
_GENERATED_DART_SUFFIXES = (
    ".g.dart", ".freezed.dart", ".gr.dart", ".mocks.dart", ".config.dart",
)

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
        name = Path(source).name.lower()
        if name.endswith(_GENERATED_DART_SUFFIXES):
            return False
        return Path(source).suffix.lower() in _CODE_EXTENSIONS

    async def parse(self, source: str) -> list[ParsedChunk]:
        path = Path(source)
        text = path.read_text(encoding="utf-8", errors="replace")
        lang = path.suffix.lower().lstrip(".")

        if path.suffix == ".py":
            return self._parse_python(text, source)
        if path.suffix.lower() == ".dart":
            try:
                return parse_dart(text, source)
            except (DartParseError, RecursionError, IndexError):
                pass  # unparseable: generic chunking below
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

        def make_chunk(
            node: ast.AST, name: str, kind: str, end_line: int | None = None
        ) -> ParsedChunk:
            start = node.lineno - 1  # type: ignore[attr-defined]
            end = end_line if end_line is not None else (node.end_lineno or start + 1)  # type: ignore[attr-defined]
            snippet = "\n".join(lines[start:end])
            docstring = ast.get_docstring(node) or ""  # type: ignore[arg-type]
            return ParsedChunk(
                content=snippet,
                title=name,
                metadata={
                    "language": "python",
                    "file": source,
                    "name": name,
                    "kind": kind,
                    "docstring": docstring[:200],
                    "line_start": node.lineno,  # type: ignore[attr-defined]
                    "line_end": end,
                },
            )

        # Walk top-level defs only — ast.walk would emit every method twice
        # (inside its class chunk AND standalone). Classes are split into a
        # header chunk plus one chunk per method, so no content is duplicated
        # and symbol search still resolves methods.
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chunks.append(make_chunk(node, node.name, "function"))
            elif isinstance(node, ast.ClassDef):
                methods = [
                    n for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                header_end = (methods[0].lineno - 1) if methods else node.end_lineno
                chunks.append(make_chunk(node, node.name, "class", end_line=header_end))
                for method in methods:
                    chunks.append(
                        make_chunk(method, f"{node.name}.{method.name}", "method")
                    )
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

