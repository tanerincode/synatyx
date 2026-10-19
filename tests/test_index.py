from __future__ import annotations

import types
from pathlib import Path
from typing import Any

from qdrant_client.models import MatchText, MatchValue

from src.core.index import (
    IndexSearchService,
    IndexService,
    chunk_point_id,
)


class FakeEmbedder:
    """Deterministic embedder; vector_map lets a test steer specific chunks."""

    def __init__(self, vector_map: dict[str, list[float]] | None = None) -> None:
        self.vector_map = vector_map or {}
        self.embed_calls = 0

    def _vec(self, text: str) -> list[float]:
        for key, vec in self.vector_map.items():
            if key in text:
                return vec
        return [1.0, 0.0, 0.0]

    async def embed(self, text: str) -> list[float]:
        return self._vec(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls += len(texts)
        return [self._vec(t) for t in texts]


def _matches(payload: dict[str, Any], cond: Any) -> bool:
    value = payload.get(cond.key)
    if getattr(cond, "range", None) is not None:
        rng = cond.range
        if rng.gte is not None and (value is None or value < rng.gte):
            return False
        return True
    match = cond.match
    if isinstance(match, MatchText):
        tokens = match.text.lower().split()
        hay = str(value or "").lower()
        return all(t in hay for t in tokens)
    if isinstance(match, MatchValue):
        return value == match.value
    return False


class FakeIndexStorage:
    """In-memory stand-in for the ctx_<slug>__index QdrantStorage surface."""

    collection_name = "ctx_test__index"

    def __init__(self) -> None:
        self.points: dict[str, tuple[list[float], dict[str, Any]]] = {}
        self.client = types.SimpleNamespace(query_points=self._query_points)

    def _records(self, conditions: list[Any]) -> list[Any]:
        out = []
        for pid, (_vec, payload) in self.points.items():
            if all(_matches(payload, c) for c in conditions):
                out.append(types.SimpleNamespace(id=pid, payload=dict(payload)))
        return out

    async def upsert_points(self, points: list[Any]) -> None:
        for p in points:
            self.points[str(p.id)] = (list(p.vector), dict(p.payload))

    async def set_payload(self, payload: dict[str, Any], ids: list[str]) -> None:
        for pid in ids:
            if pid in self.points:
                vec, existing = self.points[pid]
                self.points[pid] = (vec, {**existing, **payload})

    async def delete_by_conditions(self, conditions: list[Any]) -> None:
        for r in self._records(conditions):
            self.points.pop(r.id, None)

    async def scroll_by_conditions(self, conditions, limit=1000, offset=None, with_payload=True):
        return self._records(conditions)[:limit], None

    async def text_search(self, conditions, limit=20):
        return self._records(conditions)[:limit]

    async def _query_points(self, collection_name, query, query_filter, limit, with_payload=True):
        def cosine(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            na = sum(x * x for x in a) ** 0.5
            nb = sum(y * y for y in b) ** 0.5
            return dot / (na * nb) if na and nb else 0.0

        scored = []
        for pid, (vec, payload) in self.points.items():
            if all(_matches(payload, c) for c in query_filter.must):
                scored.append(
                    types.SimpleNamespace(id=pid, payload=dict(payload), score=cosine(query, vec))
                )
        scored.sort(key=lambda r: r.score, reverse=True)
        return types.SimpleNamespace(points=scored[:limit])


def _service(tmp_path: Path) -> tuple[IndexService, FakeIndexStorage, FakeEmbedder]:
    storage = FakeIndexStorage()
    embedder = FakeEmbedder()
    svc = IndexService(storage, "test", embedder=embedder)  # type: ignore[arg-type]
    return svc, storage, embedder


def _md(headings: int) -> str:
    return "\n\n".join(f"## Section {i}\n\ncontent {i}" for i in range(headings))


# ── IndexService ─────────────────────────────────────────────────────────────

async def test_index_file_and_reindex_unchanged(tmp_path: Path) -> None:
    f = tmp_path / "doc.md"
    f.write_text(_md(3))
    svc, storage, embedder = _service(tmp_path)

    first = await svc.index(str(f), "u1")
    assert first.files_indexed == 1
    assert first.chunks_upserted > 0
    count = len(storage.points)
    calls = embedder.embed_calls

    second = await svc.index(str(f), "u1")
    assert second.files_unchanged == 1
    assert second.chunks_upserted == 0
    assert len(storage.points) == count
    assert embedder.embed_calls == calls  # nothing re-embedded


async def test_index_deterministic_ids(tmp_path: Path) -> None:
    f = tmp_path / "doc.md"
    f.write_text(_md(2))
    svc, storage, _ = _service(tmp_path)

    await svc.index(str(f), "u1")
    ids_before = set(storage.points)
    await svc.index(str(f), "u1", force=True)
    assert set(storage.points) == ids_before
    assert chunk_point_id("test", "doc.md", 0) in ids_before


async def test_index_force_reembeds(tmp_path: Path) -> None:
    f = tmp_path / "doc.md"
    f.write_text(_md(2))
    svc, _, embedder = _service(tmp_path)
    await svc.index(str(f), "u1")
    calls = embedder.embed_calls
    result = await svc.index(str(f), "u1", force=True)
    assert result.files_indexed == 1
    assert embedder.embed_calls > calls


async def test_index_shrink_sweeps_stale_chunks(tmp_path: Path) -> None:
    f = tmp_path / "doc.md"
    f.write_text(_md(10))
    svc, storage, _ = _service(tmp_path)
    await svc.index(str(f), "u1")
    assert len(storage.points) == 10

    f.write_text(_md(4))
    result = await svc.index(str(f), "u1")
    assert result.chunks_deleted == 6
    assert len(storage.points) == 4


async def test_index_dir_removes_vanished_files(tmp_path: Path) -> None:
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text(_md(1))
    b.write_text(_md(1))
    svc, storage, _ = _service(tmp_path)
    await svc.index(str(tmp_path), "u1")
    assert len(storage.points) == 2

    b.unlink()
    result = await svc.index(str(tmp_path), "u1")
    assert len(storage.points) == 1
    assert any(d.get("status") == "removed" for d in result.details)


async def test_index_dir_skips_excluded_binary_oversized(tmp_path: Path) -> None:
    (tmp_path / "keep.md").write_text(_md(1))
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "dep.md").write_text(_md(1))
    (tmp_path / "binary.py").write_bytes(b"\x00\x01\x02" * 10)
    (tmp_path / "huge.md").write_text("x" * 300_000)
    svc, storage, _ = _service(tmp_path)

    await svc.index(str(tmp_path), "u1")
    paths = {p["path"] for _, p in storage.points.values()}
    assert paths == {"keep.md"}


async def test_index_code_survives_verbatim(tmp_path: Path) -> None:
    # regression: code goes straight to the index — no memory sanitizer that
    # would redact "system:"-looking lines
    f = tmp_path / "mod.py"
    f.write_text('def handler():\n    """system: do things"""\n    return "### instruction"\n')
    svc, storage, _ = _service(tmp_path)
    await svc.index(str(f), "u1")
    contents = [p["content"] for _, p in storage.points.values()]
    assert any("system: do things" in c for c in contents)


async def test_index_python_symbols(tmp_path: Path) -> None:
    f = tmp_path / "mod.py"
    f.write_text(
        "class Widget:\n"
        '    """A widget."""\n'
        "    def render(self):\n"
        "        return 1\n"
        "\n"
        "def helper():\n"
        "    return 2\n"
    )
    svc, storage, _ = _service(tmp_path)
    await svc.index(str(f), "u1")
    symbols = {p["symbol"] for _, p in storage.points.values()}
    assert {"Widget", "Widget.render", "helper"} <= symbols


async def test_index_status_reports_stale_and_missing(tmp_path: Path) -> None:
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text(_md(1))
    b.write_text(_md(1))
    svc, _, _ = _service(tmp_path)
    await svc.index(str(tmp_path), "u1")

    a.write_text(_md(2))  # changed on disk
    b.unlink()            # deleted on disk

    status = await svc.status("u1")
    assert status["files"] == 2
    assert "a.md" in status["stale_files"]
    assert "b.md" in status["missing_files"]
    assert status["by_language"] == {"md": 2}


# ── IndexSearchService ───────────────────────────────────────────────────────

async def _seed_code_index(
    tmp_path: Path, storage: FakeIndexStorage, embedder: FakeEmbedder
) -> None:
    f = tmp_path / "core.py"
    f.write_text(
        "def get_storage_for(name):\n    return name\n"
        "\n"
        "def unrelated_thing():\n    return 'databases are cool'\n"
    )
    svc = IndexService(storage, "test", embedder=embedder)  # type: ignore[arg-type]
    await svc.index(str(f), "u1")


async def test_search_exact_symbol_found_when_dense_misses(tmp_path: Path) -> None:
    storage = FakeIndexStorage()
    # symbol chunk gets a vector orthogonal to every query embedding
    embedder = FakeEmbedder({"get_storage_for": [0.0, 1.0, 0.0]})
    await _seed_code_index(tmp_path, storage, embedder)

    search = IndexSearchService(storage, embedder=FakeEmbedder())  # type: ignore[arg-type]
    hits = await search.search("where is `get_storage_for` defined", "u1", top_k=2)

    assert hits, "exact-symbol pass should surface the chunk dense search missed"
    top = hits[0]
    assert top["symbol"] == "get_storage_for"
    assert top["match"]["exact_symbol"] is True


async def test_search_fulltext_finds_identifier_without_backticks(tmp_path: Path) -> None:
    storage = FakeIndexStorage()
    embedder = FakeEmbedder({"get_storage_for": [0.0, 1.0, 0.0]})
    await _seed_code_index(tmp_path, storage, embedder)

    search = IndexSearchService(storage, embedder=FakeEmbedder())  # type: ignore[arg-type]
    hits = await search.search("how does get_storage_for work", "u1", top_k=2)
    assert any(h["symbol"] == "get_storage_for" for h in hits)


async def test_search_language_and_path_filters(tmp_path: Path) -> None:
    storage = FakeIndexStorage()
    embedder = FakeEmbedder()
    await _seed_code_index(tmp_path, storage, embedder)

    search = IndexSearchService(storage, embedder=FakeEmbedder())  # type: ignore[arg-type]
    assert await search.search("storage", "u1", language="ts") == []
    assert await search.search("storage", "u1", path_prefix="nonexistent/") == []
    assert await search.search("storage", "u1", path_prefix="core") != []


async def test_search_flags_stale_hit(tmp_path: Path) -> None:
    storage = FakeIndexStorage()
    embedder = FakeEmbedder()
    await _seed_code_index(tmp_path, storage, embedder)
    (tmp_path / "core.py").write_text("def get_storage_for(name):\n    return name.upper()\n")

    search = IndexSearchService(storage, embedder=FakeEmbedder())  # type: ignore[arg-type]
    hits = await search.search("`get_storage_for`", "u1", top_k=1)
    assert hits[0].get("possibly_stale") is True


# ── push indexing (diff / content upload) ────────────────────────────────────

async def test_diff_files_reports_changed_and_removed(tmp_path: Path) -> None:
    f = tmp_path / "doc.md"
    f.write_text(_md(2))
    svc, storage, _ = _service(tmp_path)
    await svc.index(str(f), "u1")
    server_hash = next(p["file_hash"] for _, p in storage.points.values())

    diff = await svc.diff_files("u1", {
        "doc.md": server_hash,        # unchanged
        "new.py": "abc123",           # not on server → changed
    })
    assert diff["changed"] == ["new.py"]
    assert diff["removed"] == []
    assert diff["unchanged"] == 1

    diff2 = await svc.diff_files("u1", {"other.md": "zzz"})
    assert diff2["removed"] == ["doc.md"]
    assert "other.md" in diff2["changed"]


async def test_index_content_roundtrip(tmp_path: Path) -> None:
    svc, storage, _ = _service(tmp_path)
    code = "def pushed_function():\n    return 42\n"
    result = await svc.index_content("u1", [{"path": "src/pushed.py", "content": code}])
    assert result.files_indexed == 1
    payloads = [p for _, p in storage.points.values()]
    assert payloads[0]["path"] == "src/pushed.py"
    assert payloads[0]["abs_path"] is None          # no server-side file
    assert payloads[0]["symbol"] == "pushed_function"
    assert payloads[0]["language"] == "python"

    # idempotent: same content again → unchanged... (hash matches at chunk level,
    # file-level skip happens in diff_files on the client path)
    again = await svc.index_content("u1", [{"path": "src/pushed.py", "content": code}])
    assert again.chunks_upserted == 0


async def test_index_content_then_diff_unchanged(tmp_path: Path) -> None:
    svc, _, _ = _service(tmp_path)
    import hashlib as _h
    code = "## Notes\n\npushed doc\n"
    await svc.index_content("u1", [{"path": "notes.md", "content": code}])
    digest = _h.sha256(code.encode()).hexdigest()[:16]
    diff = await svc.diff_files("u1", {"notes.md": digest})
    assert diff["changed"] == []
    assert diff["unchanged"] == 1


async def test_remove_files_sweeps_chunks(tmp_path: Path) -> None:
    svc, storage, _ = _service(tmp_path)
    await svc.index_content("u1", [{"path": "a.md", "content": _md(1)}])
    assert storage.points
    deleted = await svc.remove_files("u1", ["a.md"])
    assert deleted >= 1
    assert not storage.points


async def test_status_skips_pushed_files_for_staleness(tmp_path: Path) -> None:
    svc, _, _ = _service(tmp_path)
    await svc.index_content("u1", [{"path": "remote.py", "content": "def f():\n    pass\n"}])
    status = await svc.status("u1")
    assert status["files"] == 1
    assert status["missing_files"] == []   # abs_path is None → not "missing"


# ── watch-root discovery ─────────────────────────────────────────────────────

def test_discover_projects_repo_root(tmp_path: Path) -> None:
    from src.core.index import discover_projects
    (tmp_path / ".git").mkdir()
    assert discover_projects(tmp_path) == [(tmp_path.name.lower().replace("-", "_"), tmp_path)]


def test_discover_projects_workspace_root(tmp_path: Path) -> None:
    from src.core.index import discover_projects
    (tmp_path / "proj-a").mkdir()
    (tmp_path / "proj-b").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "loose-file.txt").write_text("x")
    found = discover_projects(tmp_path)
    assert [slug for slug, _ in found] == ["proj_a", "proj_b"]
