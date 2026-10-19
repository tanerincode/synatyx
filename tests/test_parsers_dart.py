from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from src.core.index import IndexService
from src.parsers.code import CodeParser
from src.parsers.registry import has_file_parser

FIXTURE = Path(__file__).parent / "fixtures" / "flutter_app"
MAIN = str(FIXTURE / "lib" / "main.dart")


async def _parse(path: str) -> list[Any]:
    return await CodeParser().parse(path)


async def _symbols() -> dict[tuple[str, str], tuple[int, int]]:
    return {
        (c.metadata["kind"], c.metadata["name"]): (
            c.metadata["line_start"], c.metadata["line_end"],
        )
        for c in await _parse(MAIN)
    }


async def test_top_level_symbols() -> None:
    sym = await _symbols()
    assert sym[("typedef", "Json")] == (3, 3)
    assert sym[("function", "main")] == (5, 7)
    assert sym[("function", "greet")] == (9, 9)  # `{...}` inside the string is ignored
    assert sym[("getter", "answer")] == (11, 11)
    assert sym[("mixin", "Logger")] == (72, 72)
    assert sym[("class", "Shape")] == (26, 26)   # abstract
    assert sym[("class", "Result")] == (30, 30)  # sealed


async def test_class_header_plus_members_and_doc_comment() -> None:
    sym = await _symbols()
    # header chunk starts at the `///` doc line and stops before the first member
    assert sym[("class", "MyApp")] == (13, 14)
    assert sym[("constructor", "MyApp.new")] == (15, 15)
    assert sym[("method", "MyApp.build")] == (17, 23)

    chunks = {c.metadata["name"]: c for c in await _parse(MAIN)}
    assert chunks["MyApp"].metadata["docstring"] == "The root widget."
    assert "build" not in chunks["MyApp"].content  # members are not duplicated


async def test_build_methods_are_their_own_chunks() -> None:
    builds = [
        c for c in await _parse(MAIN) if c.metadata["name"].endswith(".build")
    ]
    assert [c.metadata["name"] for c in builds] == ["MyApp.build", "_CounterState.build"]
    for c in builds:
        assert c.metadata["kind"] == "method"
        assert "Widget build(BuildContext context)" in c.content
        assert c.content.lstrip().startswith("@override")
    # the `}` / `{` inside string interpolation did not end the method early
    assert builds[1].metadata["line_end"] == 69
    assert "ElevatedButton" in builds[1].content


async def test_stateful_widget_and_state() -> None:
    sym = await _symbols()
    assert sym[("class", "Counter")] == (32, 32)
    assert sym[("constructor", "Counter.new")] == (33, 33)
    assert sym[("method", "Counter.createState")] == (37, 38)
    assert sym[("class", "_CounterState")] == (41, 43)


async def test_constructors_getters_setters() -> None:
    sym = await _symbols()
    assert sym[("constructor", "_CounterState.new")] == (44, 44)
    assert sym[("constructor", "_CounterState.named")] == (46, 46)
    assert sym[("constructor", "_CounterState.zero")] == (48, 50)  # factory
    assert sym[("getter", "_CounterState.count")] == (52, 52)
    assert sym[("setter", "_CounterState.count")] == (54, 56)
    # braces and `}` in comments / closures did not break method ranges
    assert sym[("method", "_CounterState._inc")] == (58, 63)


async def test_extensions_and_enum() -> None:
    sym = await _symbols()
    assert ("extension", "StringX") in sym
    assert ("method", "StringX.shout") in sym
    assert ("extension", "extension on int") in sym  # unnamed
    assert ("getter", "extension on int.doubled") in sym
    assert ("enum", "Color") in sym
    assert ("constructor", "Color.new") in sym
    assert ("method", "Color.label") in sym
    # enum constants stay in the header chunk
    chunks = {c.metadata["name"]: c for c in await _parse(MAIN)}
    assert "red(1)" in chunks["Color"].content


async def test_every_chunk_has_dart_metadata_and_ranges_match_content() -> None:
    lines = Path(MAIN).read_text().split("\n")
    for c in await _parse(MAIN):
        meta = c.metadata
        assert meta["language"] == "dart"
        assert meta["file"] == MAIN
        a, b = meta["line_start"], meta["line_end"]
        assert c.content == "\n".join(lines[a - 1:b])


async def test_unparseable_dart_falls_back_to_one_chunk(tmp_path: Path) -> None:
    f = tmp_path / "broken.dart"
    f.write_text("class Broken {\n  void f() {\n")
    chunks = await _parse(str(f))
    assert len(chunks) == 1
    assert chunks[0].metadata["language"] == "dart"
    assert chunks[0].content.startswith("class Broken")


async def test_no_declarations_falls_back_to_one_chunk(tmp_path: Path) -> None:
    f = tmp_path / "consts.dart"
    f.write_text("const a = 1;\nfinal b = {'k': 1};\n")
    chunks = await _parse(str(f))
    assert len(chunks) == 1 and chunks[0].metadata["language"] == "dart"


@pytest.mark.parametrize(
    "name",
    ["a.g.dart", "a.freezed.dart", "a.gr.dart", "a.mocks.dart", "a.config.dart"],
)
def test_generated_dart_is_not_supported(name: str) -> None:
    assert not CodeParser.supports(f"lib/{name}")
    assert not has_file_parser(f"lib/{name}")


def test_plain_dart_and_pubspec_are_supported() -> None:
    assert CodeParser.supports("lib/main.dart")
    assert CodeParser.supports("pubspec.yaml")


async def test_pubspec_is_indexed_as_yaml() -> None:
    chunks = await _parse(str(FIXTURE / "pubspec.yaml"))
    assert chunks and all(c.metadata["language"] == "yaml" for c in chunks)


async def test_walk_skips_generated_and_build_dirs(tmp_path: Path) -> None:
    root = tmp_path / "app"
    shutil.copytree(FIXTURE, root)
    for rel in (".dart_tool/x.dart", "build/y.dart", "ios/Pods/z.dart", "android/.gradle/w.dart"):
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("class Hidden {}\n")
    for name in ("pubspec.lock", ".flutter-plugins", ".flutter-plugins-dependencies"):
        (root / name).write_text("x: 1\n")

    svc = IndexService.__new__(IndexService)
    files, _, _ = await svc._resolve_files(str(root), max_files=100)
    rels = sorted(str(f.relative_to(root)) for f in files)
    assert rels == ["lib/main.dart", "pubspec.yaml"]


async def test_braces_in_raw_strings_and_comments_are_ignored(tmp_path: Path) -> None:
    f = tmp_path / "s.dart"
    f.write_text(
        "String a() => r'{ raw }';\n"
        "/* } nested /* } */ } */\n"
        "String b() {\n"
        "  return '''{\n}''' + \"\\\"}\" + '${ {'k': 1}['k'] }';\n"
        "}\n"
        "int c() => 1;\n"
    )
    sym = {c.metadata["name"]: (c.metadata["line_start"], c.metadata["line_end"])
           for c in await _parse(str(f))}
    assert sym == {"a": (1, 1), "b": (3, 6), "c": (7, 7)}
