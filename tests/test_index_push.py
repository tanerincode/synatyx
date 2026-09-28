from __future__ import annotations

import types
from typing import Any

import pytest

from src.core.index import (
    PUSH_MAX_BYTES,
    PUSH_MAX_FILES,
    REMOVE_MAX_PATHS,
    IndexService,
    validate_push_files,
    validate_remove_paths,
)
from src.transports.mcp.server import SynatyxMCPServer
from tests.test_index import FakeEmbedder, FakeIndexStorage


def _server() -> tuple[SynatyxMCPServer, FakeIndexStorage, list[str | None]]:
    """A server whose only wired part is one project's index service."""
    storage = FakeIndexStorage()
    svc = IndexService(storage, "repo-a", embedder=FakeEmbedder())  # type: ignore[arg-type]
    routed: list[str | None] = []
    server = SynatyxMCPServer.__new__(SynatyxMCPServer)
    server._pack_svc_cache = {"ctx_repo_a": object()}

    async def _get_services(user_id: str, project: str | None = None):
        return types.SimpleNamespace(collection_name=f"ctx_{project}"), None, None, None, None

    async def _get_index_services(user_id: str, project: str | None = None):
        routed.append(project)
        return svc, None

    server._get_services = _get_services  # type: ignore[method-assign]
    server._get_index_services = _get_index_services  # type: ignore[method-assign]
    return server, storage, routed


def _paths(storage: FakeIndexStorage) -> set[str]:
    return {p["path"] for _vec, p in storage.points.values()}


async def test_push_indexes_files_into_the_named_project():
    server, storage, routed = _server()
    result = await server._dispatch("context_index_push", {
        "user_id": "review-service",
        "project": "Repo A",
        "files": [
            {"path": "src/a.py", "content": "def a():\n    return 1\n"},
            {"path": "README.md", "content": "## Intro\n\nhello\n"},
        ],
    })

    assert routed == ["repo_a"]  # slugified, never the active pointer
    assert result["files_indexed"] == 2
    assert _paths(storage) == {"src/a.py", "README.md"}
    # a fresh push must be visible to the next context_pack
    assert "ctx_repo_a" not in server._pack_svc_cache


async def test_push_again_reembeds_nothing():
    server, _, _ = _server()
    args = {
        "user_id": "review-service",
        "project": "repo-a",
        "files": [{"path": "src/a.py", "content": "def a():\n    return 1\n"}],
    }
    await server._dispatch("context_index_push", dict(args))
    again = await server._dispatch("context_index_push", dict(args))

    # the skip is per chunk: nothing is re-embedded
    assert again["chunks_upserted"] == 0


async def test_remove_deletes_every_chunk_of_the_paths():
    server, storage, _ = _server()
    await server._dispatch("context_index_push", {
        "user_id": "review-service",
        "project": "repo-a",
        "files": [
            {"path": "src/a.py", "content": "def a():\n    return 1\n"},
            {"path": "src/b.py", "content": "def b():\n    return 2\n"},
        ],
    })
    result = await server._dispatch("context_index_remove", {
        "user_id": "review-service",
        "project": "repo-a",
        "paths": ["/src/a.py"],
    })

    assert result["paths_removed"] == 1
    assert result["chunks_deleted"] >= 1
    assert _paths(storage) == {"src/b.py"}


async def test_chunks_are_owned_by_the_pushing_user():
    server, storage, _ = _server()
    await server._dispatch("context_index_push", {
        "user_id": "review-service",
        "project": "repo-a",
        "files": [{"path": "a.py", "content": "x = 1\n"}],
    })
    removed = await server._dispatch("context_index_remove", {
        "user_id": "someone-else", "project": "repo-a", "paths": ["a.py"],
    })

    assert removed["chunks_deleted"] == 0
    assert _paths(storage) == {"a.py"}


@pytest.mark.parametrize("tool", ["context_index_push", "context_index_remove"])
async def test_push_and_remove_require_an_explicit_project(tool: str):
    server, _, routed = _server()
    with pytest.raises(ValueError, match="requires the project"):
        await server._dispatch(tool, {
            "user_id": "u", "files": [{"path": "a", "content": "b"}], "paths": ["a"],
        })
    assert routed == []


async def test_a_bad_batch_indexes_nothing():
    server, storage, _ = _server()
    with pytest.raises(ValueError, match=r"files\[1\]\.content"):
        await server._dispatch("context_index_push", {
            "user_id": "u",
            "project": "repo-a",
            "files": [{"path": "a.py", "content": "x = 1\n"}, {"path": "b.py"}],
        })
    assert storage.points == {}


# ── batch validation ────────────────────────────────────────────────────────

def test_validate_push_accepts_a_batch_at_the_limits():
    size = PUSH_MAX_BYTES // PUSH_MAX_FILES
    files = [{"path": f"f{i}.txt", "content": "x" * size} for i in range(PUSH_MAX_FILES)]
    assert len(validate_push_files(files)) == PUSH_MAX_FILES


@pytest.mark.parametrize(
    ("files", "message"),
    [
        (None, "non-empty list"),
        ([], "non-empty list"),
        ([{"path": f"f{i}", "content": ""} for i in range(PUSH_MAX_FILES + 1)], "too many files"),
        ([{"path": "big", "content": "x" * (PUSH_MAX_BYTES + 1)}], "split into batches"),
        (["a.py"], r"files\[0\] must be an object"),
        ([{"path": " ", "content": "x"}], r"files\[0\]\.path"),
        ([{"path": "a.py", "content": 3}], r"files\[0\]\.content"),
        ([{"path": "../etc/passwd", "content": "x"}], r"must not contain '\.\.'"),
    ],
)
def test_validate_push_rejects(files: Any, message: str):
    with pytest.raises(ValueError, match=message):
        validate_push_files(files)


def test_validate_push_counts_bytes_not_characters():
    # 'ç' is two bytes in UTF-8
    files = [{"path": "a.txt", "content": "ç" * (PUSH_MAX_BYTES // 2 + 1)}]
    with pytest.raises(ValueError, match="bytes"):
        validate_push_files(files)


def test_validate_remove_strips_leading_slashes():
    assert validate_remove_paths(["/a.py", "b/c.py "]) == ["a.py", "b/c.py"]


@pytest.mark.parametrize(
    ("paths", "message"),
    [
        (None, "non-empty list"),
        ([], "non-empty list"),
        (["a"] * (REMOVE_MAX_PATHS + 1), "too many paths"),
        (["a", ""], r"paths\[1\]"),
        ([3], r"paths\[0\]"),
    ],
)
def test_validate_remove_rejects(paths: Any, message: str):
    with pytest.raises(ValueError, match=message):
        validate_remove_paths(paths)
