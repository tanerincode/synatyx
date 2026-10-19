"""Symbol-aware chunking for Dart.

No parser dependency: the source is first masked (comments and string
contents, including ''' / \"\"\" strings and ${...} interpolation, become
spaces), so a brace counter can walk declarations safely. Output follows the
Python path: a header chunk per class-like declaration plus one chunk per
member, and one chunk per top-level function / getter / setter / typedef.

parse_dart() raises DartParseError on input it cannot make sense of (unbalanced
braces); the caller falls back to generic chunking.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from typing import Any

from src.parsers.base import ParsedChunk


class DartParseError(Exception):
    pass


# ── masking ──────────────────────────────────────────────────────────────────


def _is_ident(c: str) -> bool:
    return c.isalnum() or c in "_$"


def _string_end(text: str, i: int, raw: bool) -> int:
    """Index just past the string literal whose first quote is at text[i]."""
    n = len(text)
    q = text[i]
    triple = text.startswith(q * 3, i)
    i += 3 if triple else 1
    while i < n:
        c = text[i]
        if not raw and c == "\\":
            i += 2
            continue
        if triple:
            if text.startswith(q * 3, i):
                return i + 3
        else:
            if c == q:
                return i + 1
            if c == "\n":
                return i  # unterminated single-line string: stop at EOL
        if not raw and c == "$" and i + 1 < n and text[i + 1] == "{":
            i = _skip_interpolation(text, i + 2)
            continue
        i += 1
    return n


def _skip_interpolation(text: str, i: int) -> int:
    """i is just past '${'; return the index just past the matching '}'."""
    n = len(text)
    depth = 1
    while i < n:
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        elif c in "'\"":
            i = _string_end(text, i, False)
            continue
        i += 1
    return n


def mask(text: str) -> str:
    """Same-length copy of text with comments and string contents blanked."""
    out = list(text)
    n = len(text)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    i = 0
    while i < n:
        c = text[i]
        if c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j == -1 else j
            blank(i, j)
            i = j
        elif c == "/" and text.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("/*", j):
                    depth += 1
                    j += 2
                elif text.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            blank(i, j)
            i = j
        elif c in "'\"":
            j = _string_end(text, i, False)
            blank(i, j)
            i = j
        elif (
            c == "r"
            and i + 1 < n
            and text[i + 1] in "'\""
            and (i == 0 or not _is_ident(text[i - 1]))
        ):
            j = _string_end(text, i + 1, True)
            blank(i, j)
            i = j
        else:
            i += 1
    return "".join(out)


# ── statement scanning ───────────────────────────────────────────────────────


@dataclass
class Stmt:
    start: int          # first code char
    head_end: int       # index of the terminator ('{', ';' or start of '=>')
    term: str           # '{', ';' or '=>'
    end: int            # index of the last char (closing '}' or ';')
    body_open: int = -1
    body_close: int = -1


def _match_brace(m: str, open_idx: int) -> int:
    depth = 0
    for k in range(open_idx, len(m)):
        if m[k] == "{":
            depth += 1
        elif m[k] == "}":
            depth -= 1
            if depth == 0:
                return k
    raise DartParseError("unbalanced braces")


def _arrow_end(m: str, i: int, limit: int) -> int:
    depth = 0
    for k in range(i, limit):
        c = m[k]
        if c in "({[":
            depth += 1
        elif c in ")}]":
            depth -= 1
        elif c == ";" and depth <= 0:
            return k
    raise DartParseError("unterminated expression body")


def scan(m: str, start: int, end: int) -> list[Stmt]:
    stmts: list[Stmt] = []
    i = start
    while i < end:
        while i < end and m[i].isspace():
            i += 1
        if i >= end:
            break
        s0 = i
        paren = 0
        j = i
        found: Stmt | None = None
        while j < end:
            c = m[j]
            if c in "([":
                paren += 1
            elif c in ")]":
                paren = max(0, paren - 1)
            elif paren == 0:
                if c == ";":
                    found = Stmt(s0, j, ";", j)
                    break
                if c == "=" and j + 1 < end and m[j + 1] == ">":
                    found = Stmt(s0, j, "=>", _arrow_end(m, j + 2, end))
                    break
                if c == "{":
                    prev = m[s0:j].rstrip()[-1:]
                    close = _match_brace(m, j)
                    if prev in ("=", ",", ":", "?"):
                        j = close + 1  # expression brace (set/map literal)
                        continue
                    found = Stmt(s0, j, "{", close, j, close)
                    break
                if c == "}":
                    raise DartParseError("stray '}'")
            j += 1
        if found is None:
            break  # trailing text without a terminator
        stmts.append(found)
        i = found.end + 1
    return stmts


# ── classification ───────────────────────────────────────────────────────────


def _strip_annotations(s: str) -> str:
    s = s.lstrip()
    while s.startswith("@"):
        k = 1
        while k < len(s) and (_is_ident(s[k]) or s[k] == "."):
            k += 1
        rest = s[k:].lstrip()
        if rest.startswith("("):
            depth = 0
            for idx, c in enumerate(rest):
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        rest = rest[idx + 1:]
                        break
            else:
                return ""
        s = rest.lstrip()
    return s


def _collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


_CLASS_RE = re.compile(r"^(?:(?:abstract|sealed|base|final|interface|mixin)\s+)*class\s+(\w+)")
_MIXIN_RE = re.compile(r"^(?:base\s+)?mixin\s+(?!class\b)(\w+)")
_ENUM_RE = re.compile(r"^enum\s+(\w+)")
_EXT_TYPE_RE = re.compile(r"^extension\s+type\s+(?:const\s+)?(\w+)")
_TYPEDEF_RE = re.compile(r"^typedef\s+(\w+)")
_DIRECTIVE_RE = re.compile(r"^(?:import|export|part|library)\b")
_GETTER_RE = re.compile(r"^(.*?)\bget\s+(\w+)\s*$")
_SETTER_RE = re.compile(r"^(.*?)\bset\s+(\w+)\s*\(")
_OPERATOR_RE = re.compile(r"^(.*?)\boperator\s*([^\s(]+)\s*\(")
_NOT_CALLABLE = {"if", "for", "while", "switch", "catch", "return", "else", "do"}


def _strip_generics_suffix(s: str) -> str:
    s = s.rstrip()
    while s.endswith(">"):
        depth = 0
        for k in range(len(s) - 1, -1, -1):
            if s[k] == ">":
                depth += 1
            elif s[k] == "<":
                depth -= 1
                if depth == 0:
                    s = s[:k].rstrip()
                    break
        else:
            break
    return s


def _classify_type_decl(s: str) -> tuple[str, str] | None:
    for kind, rx in (
        ("extension_type", _EXT_TYPE_RE),
        ("class", _CLASS_RE),
        ("mixin", _MIXIN_RE),
        ("enum", _ENUM_RE),
    ):
        mt = rx.match(s)
        if mt:
            return kind, mt.group(1)
    if re.match(r"^extension\b", s):
        rest = s[len("extension"):]
        parts = re.split(r"\bon\b", rest, maxsplit=1)
        if len(parts) == 2 and not rest.lstrip().startswith("("):
            pre = _strip_generics_suffix(parts[0].strip())
            if pre.startswith("<"):  # unnamed generic: extension<T> on List<T>
                pre = ""
            nm = re.match(r"^(\w+)", pre)
            if nm:
                return "extension", nm.group(1)
            return "extension", "extension on " + parts[1].strip()
    mt = _TYPEDEF_RE.match(s)
    if mt:
        return "typedef", mt.group(1)
    return None


def _classify_callable(s: str, term: str, owner: str | None) -> tuple[str, str] | None:
    """kind, name for a function-like head, or None (field, variable, ...)."""

    def qualify(name: str) -> str:
        return f"{owner}.{name}" if owner else name

    g = _GETTER_RE.match(s)
    if g and "=" not in g.group(1):
        return "getter", qualify(g.group(2))
    st = _SETTER_RE.match(s)
    if st and "=" not in st.group(1):
        return "setter", qualify(st.group(2))
    op = _OPERATOR_RE.match(s)
    if op and "=" not in op.group(1).replace("==", ""):
        return "operator", qualify("operator" + op.group(2))

    p0 = s.find("(")
    if p0 == -1:
        return None
    pre = _strip_generics_suffix(s[:p0])
    if "=" in pre:
        return None
    if term == ";":
        depth = 0
        close = -1
        for k in range(p0, len(s)):
            if s[k] == "(":
                depth += 1
            elif s[k] == ")":
                depth -= 1
                if depth == 0:
                    close = k
                    break
        rest = s[close + 1:].strip() if close != -1 else "x"
        if rest and not rest.startswith(":"):
            # trailing text that is not an initializer list (`: super()`):
            # e.g. `void Function(int) callback;` is a field
            return None
    nm = re.search(r"([\w$]+(?:\s*\.\s*[\w$]+)?)$", pre)
    if not nm:
        return None
    name = re.sub(r"\s+", "", nm.group(1))
    if name in _NOT_CALLABLE:
        return None
    if owner is not None:
        base_owner = owner.rsplit(" ", 1)[-1] if owner.startswith("extension on ") else owner
        ctor = re.fullmatch(
            r"(?:(?:const|factory|external)\s+)*" + re.escape(base_owner) + r"(?:\s*\.\s*(\w+))?",
            pre.strip(),
        )
        if ctor:
            return "constructor", f"{owner}.{ctor.group(1) or 'new'}"
    return ("method" if owner else "function"), qualify(name.split(".")[-1])


# ── chunk assembly ───────────────────────────────────────────────────────────


def parse_dart(text: str, source: str) -> list[ParsedChunk]:
    text = text.replace("\r\n", "\n")
    m = mask(text)
    lines = text.split("\n")
    line_starts = [0]
    for idx, ch in enumerate(text):
        if ch == "\n":
            line_starts.append(idx + 1)

    def line_of(offset: int) -> int:
        return bisect.bisect_right(line_starts, offset)  # 1-based

    def lead_line(line_no: int) -> int:
        """Pull a leading /// or /** */ doc comment into the chunk."""
        ln = line_no
        while ln > 1:
            prev = lines[ln - 2].strip()
            if prev.startswith("///"):
                ln -= 1
            elif prev.endswith("*/"):
                k = ln - 2
                while k >= 0 and "/*" not in lines[k]:
                    k -= 1
                if k >= 0 and lines[k].strip().startswith("/**"):
                    ln = k + 1
                else:
                    break
            else:
                break
        return ln

    def doc_of(first_line: int, decl_line: int) -> str:
        out = []
        for ln in range(first_line, decl_line):
            t = lines[ln - 1].strip()
            if t.startswith("///"):
                out.append(t[3:].strip())
        return " ".join(out)[:200]

    def chunk(name: str, kind: str, a: int, b: int, decl_line: int) -> ParsedChunk:
        return ParsedChunk(
            content="\n".join(lines[a - 1:b]),
            title=name,
            metadata={
                "language": "dart",
                "file": source,
                "name": name,
                "kind": kind,
                "docstring": doc_of(a, decl_line),
                "line_start": a,
                "line_end": b,
            },
        )

    chunks: list[ParsedChunk] = []

    for st in scan(m, 0, len(m)):
        head = _collapse(_strip_annotations(m[st.start:st.head_end]))
        if not head or _DIRECTIVE_RE.match(head):
            continue
        decl_line = line_of(st.start)
        first = lead_line(decl_line)
        last = line_of(st.end)

        type_decl = _classify_type_decl(head)
        if type_decl is not None:
            kind, name = type_decl
            members = _members(m, st, kind, name, head, line_of, lead_line, doc_of)
            if st.term == "{" and members and members[0][2] > first:
                # header chunk up to the line before the first member
                chunks.append(chunk(name, kind, first, members[0][2] - 1, decl_line))
                for mkind, mname, mfirst, mlast, mdecl in members:
                    chunks.append(chunk(mname, mkind, mfirst, mlast, mdecl))
            else:
                chunks.append(chunk(name, kind, first, last, decl_line))
            continue

        callable_ = _classify_callable(head, st.term, None)
        if callable_ is not None:
            chunks.append(chunk(callable_[1], callable_[0], first, last, decl_line))

    if not chunks:
        raise DartParseError("no declarations found")
    return chunks


def _members(
    m: str,
    st: Stmt,
    kind: str,
    owner: str,
    head: str,
    line_of: Any,
    lead_line: Any,
    doc_of: Any,
) -> list[tuple[str, str, int, int, int]]:
    """(kind, name, first_line, last_line, decl_line) per member of a body."""
    if st.term != "{":
        return []
    start = st.body_open + 1
    end = st.body_close
    if kind == "enum":
        # constants come first, terminated by ';' — members only after it
        depth = 0
        semi = -1
        for k in range(start, end):
            c = m[k]
            if c in "({[":
                depth += 1
            elif c in ")}]":
                depth -= 1
            elif c == ";" and depth == 0:
                semi = k
                break
        if semi == -1:
            return []
        start = semi + 1
    out: list[tuple[str, str, int, int, int]] = []
    for ms in scan(m, start, end):
        mhead = _collapse(_strip_annotations(m[ms.start:ms.head_end]))
        if not mhead:
            continue
        res = _classify_callable(mhead, ms.term, owner)
        if res is None:
            continue
        decl_line = line_of(ms.start)
        out.append((res[0], res[1], lead_line(decl_line), line_of(ms.end), decl_line))
    # a one-line class would put members on the header's own line: don't split
    class_line = line_of(st.start)
    if any(first <= class_line for _, _, first, _, _ in out):
        return []
    return out
