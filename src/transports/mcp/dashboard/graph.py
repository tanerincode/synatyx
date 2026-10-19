from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

from src.transports.mcp.dashboard.common import (
    _is_memory_collection,
    _storages,
)

logger = logging.getLogger(__name__)

async def api_graph(request: Request) -> JSONResponse:
    """Nodes + relation edges for one collection — the memory graph."""
    storages = _storages(request)
    if storages is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)
    qdrant, postgres = storages

    collection = request.query_params.get("collection", "")
    valid = {n for n in await qdrant.get_all_collections() if _is_memory_collection(n)}
    if collection not in valid:
        return JSONResponse({"error": f"unknown collection {collection!r}"}, status_code=400)

    user_filter = request.query_params.get("user") or None
    try:
        limit = min(int(request.query_params.get("limit", "150")), 300)
    except ValueError:
        return JSONResponse({"error": "invalid limit"}, status_code=400)

    payloads, _next = await qdrant.scoped(collection).scan_all_items(
        include_deprecated=True, limit=1000
    )
    if user_filter:
        payloads = [p for p in payloads if p.get("user_id") == user_filter]
    payloads.sort(key=lambda p: p.get("created_at") or "", reverse=True)
    payloads = payloads[:limit]

    node_ids = [p["_id"] for p in payloads if p.get("_id")]
    edges = await postgres.relation_list_all(item_ids=node_ids, limit=500)

    # Hydrate edge endpoints that fall outside the scanned set (e.g. the other
    # arm of a supersedes chain, or an L4 item in ctx_users) so their edges
    # render instead of being dropped.
    known = {p["_id"] for p in payloads}
    scoped = qdrant.scoped(collection)
    for edge in edges:
        for endpoint in (edge.source_item_id, edge.target_item_id):
            if endpoint in known:
                continue
            known.add(endpoint)
            item = await scoped.get_by_id(endpoint)
            if item is not None:
                payloads.append(
                    {
                        "_id": item.id,
                        "user_id": item.user_id,
                        "content": item.content,
                        "memory_layer": item.memory_layer.value,
                        "importance": item.importance,
                        "is_pinned": item.is_pinned,
                        "is_deprecated": item.is_deprecated,
                        "metadata": item.metadata,
                        "created_at": item.created_at.isoformat() if item.created_at else None,
                    }
                )

    present = {p["_id"] for p in payloads}
    nodes = [
        {
            "id": p["_id"],
            "label": (p.get("content") or "")[:120],
            "memory_layer": p.get("memory_layer"),
            "importance": p.get("importance", 0.5),
            "is_pinned": p.get("is_pinned", False),
            "is_deprecated": p.get("is_deprecated", False),
            "user_id": p.get("user_id"),
            "checkpoint_name": (p.get("metadata") or {}).get("checkpoint_name"),
        }
        for p in payloads
    ]
    edge_list = [
        {
            "source": e.source_item_id,
            "target": e.target_item_id,
            "type": e.relation_type,
        }
        for e in edges
        if e.source_item_id in present and e.target_item_id in present
    ]

    return JSONResponse(
        {
            "collection": collection,
            "nodes": nodes,
            "edges": edge_list,
            "node_count": len(nodes),
            "edge_count": len(edge_list),
        }
    )
