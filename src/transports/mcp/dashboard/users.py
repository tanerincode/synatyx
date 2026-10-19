from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from src.models.memory_layer import MemoryLayer
from src.transports.mcp.dashboard.common import (
    _is_memory_collection,
    _storages,
)

logger = logging.getLogger(__name__)

async def api_users(request: Request) -> JSONResponse:
    """Distinct users in one collection with per-user memory counts."""
    storages = _storages(request)
    if storages is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)
    qdrant, _ = storages

    collection = request.query_params.get("collection", "")
    valid = {n for n in await qdrant.get_all_collections() if _is_memory_collection(n)}
    if collection not in valid:
        return JSONResponse({"error": f"unknown collection {collection!r}"}, status_code=400)

    payloads, _next = await qdrant.scoped(collection).scan_all_items(
        include_deprecated=True, limit=1000
    )

    users: dict[str, dict[str, Any]] = {}
    for p in payloads:
        uid = p.get("user_id") or "(unknown)"
        u = users.setdefault(
            uid,
            {
                "user_id": uid,
                "total": 0,
                "active": 0,
                "deprecated": 0,
                "pinned": 0,
                "by_layer": {layer.value: 0 for layer in MemoryLayer},
                "last_created_at": None,
            },
        )
        u["total"] += 1
        if p.get("is_deprecated"):
            u["deprecated"] += 1
        else:
            u["active"] += 1
            layer = p.get("memory_layer")
            if layer in u["by_layer"]:
                u["by_layer"][layer] += 1
            if p.get("is_pinned"):
                u["pinned"] += 1
        created = p.get("created_at")
        if created and (u["last_created_at"] is None or created > u["last_created_at"]):
            u["last_created_at"] = created

    ranked = sorted(users.values(), key=lambda u: u["total"], reverse=True)
    return JSONResponse({"collection": collection, "count": len(ranked), "users": ranked})
