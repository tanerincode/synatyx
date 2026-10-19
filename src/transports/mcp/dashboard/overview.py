from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

from src.models.memory_layer import MemoryLayer
from src.models.task import TaskStatus
from src.transports.mcp.dashboard.common import (
    _COLLECTION_PREFIX,
    _is_memory_collection,
    _storages,
)

logger = logging.getLogger(__name__)

async def api_overview(request: Request) -> JSONResponse:
    """Per-collection memory counts plus open-task totals."""
    storages = _storages(request)
    if storages is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)
    qdrant, postgres = storages

    names = sorted(
        n for n in await qdrant.get_all_collections() if _is_memory_collection(n)
    )
    collections = []
    for name in names:
        try:
            stats = await qdrant.scoped(name).collection_stats()
        except Exception:
            logger.exception("collection_stats failed for %s", name)
            continue
        collections.append(
            {
                "collection": name,
                "slug": name.removeprefix(_COLLECTION_PREFIX),
                "is_user_global": name == "ctx_users",
                **stats,
            }
        )

    open_tasks = await postgres.task_list_all(status=TaskStatus.PENDING, limit=200)

    totals = {
        "total": sum(c["total"] for c in collections),
        "active": sum(c["active"] for c in collections),
        "deprecated": sum(c["deprecated"] for c in collections),
        "pinned": sum(c["pinned"] for c in collections),
        "by_layer": {
            layer.value: sum(c["by_layer"].get(layer.value, 0) for c in collections)
            for layer in MemoryLayer
        },
        "collections": len(collections),
        "open_tasks": len(open_tasks),
    }

    from src.transports.mcp.http_server import server_info
    return JSONResponse({"totals": totals, "collections": collections, "server": server_info()})
