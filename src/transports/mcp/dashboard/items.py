from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from src.models.memory_layer import MemoryLayer
from src.transports.mcp.dashboard.common import (
    _is_memory_collection,
    _storages,
)

logger = logging.getLogger(__name__)

async def api_items(request: Request) -> JSONResponse:
    """Recent items in one collection, newest first. No user filter — admin view."""
    storages = _storages(request)
    if storages is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)
    qdrant, _ = storages

    collection = request.query_params.get("collection", "")
    valid = {n for n in await qdrant.get_all_collections() if _is_memory_collection(n)}
    if collection not in valid:
        return JSONResponse({"error": f"unknown collection {collection!r}"}, status_code=400)

    layer_raw = request.query_params.get("layer") or None
    layer = None
    if layer_raw:
        try:
            layer = MemoryLayer(layer_raw)
        except ValueError:
            return JSONResponse({"error": f"invalid layer {layer_raw!r}"}, status_code=400)

    include_deprecated = request.query_params.get("include_deprecated") == "true"
    user_filter = request.query_params.get("user") or None
    try:
        limit = min(int(request.query_params.get("limit", "50")), 200)
    except ValueError:
        return JSONResponse({"error": "invalid limit"}, status_code=400)

    payloads, _next = await qdrant.scoped(collection).scan_all_items(
        memory_layer=layer,
        include_deprecated=include_deprecated,
        limit=1000,
    )
    if user_filter:
        payloads = [p for p in payloads if p.get("user_id") == user_filter]

    def _created(p: dict[str, Any]) -> str:
        return p.get("created_at") or ""

    payloads.sort(key=_created, reverse=True)

    items = []
    for p in payloads[:limit]:
        meta = p.get("metadata") or {}
        content = p.get("content", "")
        items.append(
            {
                "id": p.get("_id"),
                "content": content[:400],
                "truncated": len(content) > 400,
                "memory_layer": p.get("memory_layer"),
                "importance": p.get("importance"),
                "is_pinned": p.get("is_pinned", False),
                "is_deprecated": p.get("is_deprecated", False),
                "origin": meta.get("origin"),
                "type": meta.get("type") or p.get("type"),
                "checkpoint_name": meta.get("checkpoint_name"),
                "user_id": p.get("user_id"),
                "session_id": p.get("session_id"),
                "project": p.get("project"),
                "created_at": p.get("created_at"),
            }
        )

    return JSONResponse(
        {
            "collection": collection,
            "count": len(items),
            "generated_at": datetime.now(UTC).isoformat(),
            "items": items,
        }
    )
