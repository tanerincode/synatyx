from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from src.config import settings
from src.core.hygiene import DASHBOARD_WARN_DAYS, HygieneService
from src.transports.mcp.dashboard.common import _is_memory_collection, _storages

_CONTENT_CHARS = 400


def _summary(p: dict[str, Any]) -> dict[str, Any]:
    """The item fields the hygiene page shows, in the shape /items uses."""
    meta = p.get("metadata") or {}
    content = p.get("content", "")
    out: dict[str, Any] = {
        "id": p.get("_id"),
        "content": content[:_CONTENT_CHARS],
        "truncated": len(content) > _CONTENT_CHARS,
        "memory_layer": p.get("memory_layer"),
        "importance": p.get("importance"),
        "is_pinned": p.get("is_pinned", False),
        "is_deprecated": p.get("is_deprecated", False),
        "origin": meta.get("origin"),
        "type": meta.get("type") or p.get("type"),
        "user_id": p.get("user_id"),
        "project": p.get("project"),
        "created_at": p.get("created_at"),
    }
    if p.get("possibly_stale"):
        out["possibly_stale"] = True
        out["stale_files"] = p.get("stale_files", [])
    return out


async def api_hygiene(request: Request) -> JSONResponse:
    """GC counts, duplicate suggestions and stale items for one collection."""
    storages = _storages(request)
    if storages is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)
    qdrant, postgres = storages

    collection = request.query_params.get("collection", "")
    valid = {n for n in await qdrant.get_all_collections() if _is_memory_collection(n)}
    if collection not in valid:
        return JSONResponse({"error": f"unknown collection {collection!r}"}, status_code=400)
    user_filter = request.query_params.get("user") or None

    scoped = qdrant.scoped(collection)
    payloads, _next = await scoped.scan_all_items(include_deprecated=True, limit=1000)
    if user_filter:
        payloads = [p for p in payloads if p.get("user_id") == user_filter]

    hygiene = HygieneService(scoped, postgres, settings.gc)
    counts = hygiene.gc_counts(payloads, warn_days=DASHBOARD_WARN_DAYS)
    duplicates = await hygiene.duplicates(payloads)
    stale = hygiene.stale_items(payloads)

    return JSONResponse(
        {
            "collection": collection,
            "generated_at": datetime.now(UTC).isoformat(),
            "expiring_7d": counts.expiring_soon,
            "deprecated": counts.deprecated,
            "pending_hard_delete": counts.pending_hard_delete,
            "protected": counts.protected,
            "duplicates": [
                {"a": _summary(d["a"]), "b": _summary(d["b"]), "similarity": d["similarity"]}
                for d in duplicates
            ],
            "stale": [_summary(p) for p in stale],
        }
    )
