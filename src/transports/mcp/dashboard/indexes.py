from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from src.transports.mcp.dashboard.common import (
    _COLLECTION_PREFIX,
    _storages,
)

logger = logging.getLogger(__name__)

async def api_indexes(request: Request) -> JSONResponse:
    """Per-project code/doc index stats (ctx_<slug>__index collections):
    file/chunk counts, language breakdown, last index time, users."""
    from src.core.project import INDEX_SUFFIX, is_index_collection

    storages = _storages(request)
    if storages is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)
    qdrant, _ = storages

    names = sorted(
        n for n in await qdrant.get_all_collections()
        if n.startswith(_COLLECTION_PREFIX) and is_index_collection(n)
    )
    indexes = []
    for name in names:
        scoped = qdrant.scoped(name)
        file_records: list[dict[str, Any]] = []
        offset = None
        try:
            while True:
                payloads, offset = await scoped.scan_all_items(
                    include_deprecated=True, limit=1000, offset=offset
                )
                file_records.extend(p for p in payloads if p.get("chunk_index") == 0)
                if offset is None:
                    break
        except Exception:
            logger.exception("index scan failed for %s", name)
            continue

        by_language: dict[str, int] = {}
        chunks = 0
        last: str | None = None
        users: set[str] = set()
        for p in file_records:
            lang = p.get("language") or "unknown"
            by_language[lang] = by_language.get(lang, 0) + 1
            chunks += p.get("chunk_total") or 1
            if p.get("user_id"):
                users.add(p["user_id"])
            ts = p.get("indexed_at")
            if ts and (last is None or ts > last):
                last = ts

        indexes.append({
            "collection": name,
            "project": name.removeprefix(_COLLECTION_PREFIX).removesuffix(INDEX_SUFFIX),
            "files": len(file_records),
            "chunks": chunks,
            "by_language": dict(sorted(by_language.items(), key=lambda kv: -kv[1])),
            "last_indexed_at": last,
            "users": sorted(users),
        })

    return JSONResponse({"count": len(indexes), "indexes": indexes})


async def _scan_index_collection(
    qdrant: Any, collection: str, chunk0_only: bool = False
) -> list[dict[str, Any]]:
    scoped = qdrant.scoped(collection)
    payloads: list[dict[str, Any]] = []
    offset = None
    while True:
        batch, offset = await scoped.scan_all_items(
            include_deprecated=True, limit=1000, offset=offset
        )
        payloads.extend(
            p for p in batch if not chunk0_only or p.get("chunk_index") == 0
        )
        if offset is None:
            break
    return payloads


def _valid_index_collection(name: str) -> bool:
    from src.core.project import is_index_collection
    return name.startswith(_COLLECTION_PREFIX) and is_index_collection(name)


async def api_index_graph(request: Request) -> JSONResponse:
    """File/directory tree of one code index as graph data: file nodes sized
    by chunk count, directory hub nodes, edges up the directory chain."""
    from src.core.project import INDEX_SUFFIX

    storages = _storages(request)
    if storages is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)
    qdrant, _ = storages

    collection = request.query_params.get("collection", "")
    valid = {n for n in await qdrant.get_all_collections() if _valid_index_collection(n)}
    if collection not in valid:
        return JSONResponse({"error": f"unknown index collection {collection!r}"}, status_code=400)

    files = await _scan_index_collection(qdrant, collection, chunk0_only=True)
    files.sort(key=lambda p: p.get("path") or "")
    truncated = len(files) > 400
    files = files[:400]

    project = collection.removeprefix(_COLLECTION_PREFIX).removesuffix(INDEX_SUFFIX)
    nodes: list[dict[str, Any]] = [{
        "id": "root", "label": project, "kind": "project",
        "language": None, "chunks": 0, "importance": 1.0,
    }]
    edges: list[dict[str, Any]] = []
    dirs: set[str] = set()

    def ensure_dir(dir_path: str) -> str:
        """Create the directory chain up to root; return this dir's node id."""
        if not dir_path:
            return "root"
        if dir_path not in dirs:
            dirs.add(dir_path)
            parent = dir_path.rsplit("/", 1)[0] if "/" in dir_path else ""
            nodes.append({
                "id": f"d:{dir_path}", "label": dir_path, "kind": "dir",
                "language": None, "chunks": 0, "importance": 0.3,
            })
            edges.append({"source": f"d:{dir_path}", "target": ensure_dir(parent), "type": "in"})
        return f"d:{dir_path}"

    for p in files:
        path = p.get("path") or "?"
        chunks = p.get("chunk_total") or 1
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        nodes.append({
            "id": f"f:{path}", "label": path, "kind": "file",
            "language": p.get("language") or "unknown",
            "chunks": chunks,
            "importance": min(1.0, 0.25 + chunks / 12),
            "indexed_at": p.get("indexed_at"),
        })
        edges.append({"source": f"f:{path}", "target": ensure_dir(parent), "type": "in"})

    return JSONResponse({
        "collection": collection,
        "nodes": nodes,
        "edges": edges,
        "truncated": truncated,
    })


async def api_index_chunks(request: Request) -> JSONResponse:
    """Chunk-level detail of one code index, optionally filtered by a path
    substring; ordered by path + chunk_index."""
    storages = _storages(request)
    if storages is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)
    qdrant, _ = storages

    collection = request.query_params.get("collection", "")
    valid = {n for n in await qdrant.get_all_collections() if _valid_index_collection(n)}
    if collection not in valid:
        return JSONResponse({"error": f"unknown index collection {collection!r}"}, status_code=400)

    query = (request.query_params.get("q") or "").strip().lower()
    try:
        limit = min(int(request.query_params.get("limit", "100")), 500)
    except ValueError:
        return JSONResponse({"error": "invalid limit"}, status_code=400)

    payloads = await _scan_index_collection(qdrant, collection)
    if query:
        payloads = [
            p for p in payloads
            if query in str(p.get("path", "")).lower()
            or query in str(p.get("symbol") or "").lower()
        ]
    payloads.sort(key=lambda p: (p.get("path") or "", p.get("chunk_index") or 0))

    chunks = [{
        "path": p.get("path"),
        "chunk_index": p.get("chunk_index"),
        "chunk_total": p.get("chunk_total"),
        "symbol": p.get("symbol"),
        "kind": p.get("kind"),
        "language": p.get("language"),
        "line_start": p.get("line_start"),
        "line_end": p.get("line_end"),
        "preview": str(p.get("content") or "")[:220],
        "indexed_at": p.get("indexed_at"),
    } for p in payloads[:limit]]

    return JSONResponse({
        "collection": collection,
        "total": len(payloads),
        "count": len(chunks),
        "chunks": chunks,
    })
