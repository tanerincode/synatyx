from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from src.core.staleness import check_stale_files
from src.models.memory_layer import MemoryLayer
from src.transports.mcp.dashboard.common import (
    _is_memory_collection,
    _storages,
)

logger = logging.getLogger(__name__)

_SCAN_CAP = 5000  # max items scanned (and filtered in memory) per list request
_LABEL_CHARS = 80
_ACTIONS = ("pin", "unpin", "deprecate", "restore")
_SORT_FIELDS = ("created_at", "importance")


def _meta(p: dict[str, Any]) -> dict[str, Any]:
    meta = p.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _stale_files(p: dict[str, Any]) -> list[str]:
    meta = _meta(p)
    if not meta.get("file_hashes"):
        return []
    try:
        return check_stale_files(meta)
    except Exception:
        return []


def _parse_ts(raw: str) -> datetime | None:
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _item_summary(p: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    meta = _meta(p)
    content = p.get("content", "")
    out: dict[str, Any] = {
        "id": p.get("_id"),
        "content": content if full else content[:400],
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
        "possibly_stale": bool(_stale_files(p)),
    }
    if not full:
        out["truncated"] = len(content) > 400
    return out


async def _valid_collection(qdrant: Any, collection: str) -> bool:
    valid = {n for n in await qdrant.get_all_collections() if _is_memory_collection(n)}
    return collection in valid


async def _item_detail(
    qdrant: Any, postgres: Any, collection: str, p: dict[str, Any]
) -> dict[str, Any]:
    meta = _meta(p)
    item_id = p["_id"]
    stale = _stale_files(p)
    detail = _item_summary(p, full=True)
    detail.update(
        {
            "confidence": meta.get("confidence"),
            "fact_type": meta.get("fact_type"),
            "files": meta.get("files") or [],
            "possibly_stale": bool(stale),
            "stale_files": stale,
            "deprecated_at": p.get("deprecated_at"),
            "deprecated_reason": p.get("deprecated_reason"),
            "metadata": meta,
        }
    )

    scoped = qdrant.scoped(collection)
    related = []
    for edge in await postgres.relation_list_all(item_ids=[item_id], limit=200):
        outgoing = edge.source_item_id == item_id
        other_id = edge.target_item_id if outgoing else edge.source_item_id
        other = await scoped.get_item(other_id)
        if other is None and collection != "ctx_users":
            other = await qdrant.scoped("ctx_users").get_item(other_id)
        related.append(
            {
                "id": other_id,
                "type": edge.relation_type,
                "direction": "out" if outgoing else "in",
                "label": (other.get("content", "") if other else "")[:_LABEL_CHARS],
            }
        )
    detail["related"] = related
    return detail


async def api_item_detail(request: Request) -> JSONResponse:
    """One item in full, with staleness and its relations (both directions)."""
    storages = _storages(request)
    if storages is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)
    qdrant, postgres = storages
    collection = request.query_params.get("collection", "")
    if not await _valid_collection(qdrant, collection):
        return JSONResponse({"error": f"unknown collection {collection!r}"}, status_code=400)
    p = await qdrant.scoped(collection).get_item(request.path_params["id"])
    if p is None:
        return JSONResponse({"error": "item not found"}, status_code=404)
    return JSONResponse(await _item_detail(qdrant, postgres, collection, p))


async def api_item_action(request: Request) -> JSONResponse:
    """POST /dashboard/api/items/{id}/{pin|unpin|deprecate|restore}."""
    storages = _storages(request)
    if storages is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)
    qdrant, postgres = storages
    collection = request.query_params.get("collection", "")
    if not await _valid_collection(qdrant, collection):
        return JSONResponse({"error": f"unknown collection {collection!r}"}, status_code=400)
    action = request.path_params["action"]
    if action not in _ACTIONS:
        return JSONResponse({"error": f"unknown action {action!r}"}, status_code=404)
    item_id = request.path_params["id"]
    store = qdrant.scoped(collection)
    if await store.get_item(item_id) is None:
        return JSONResponse({"error": "item not found"}, status_code=404)

    if action == "pin":
        await store.set_pinned(item_id, True)
    elif action == "unpin":
        await store.set_pinned(item_id, False)
    elif action == "restore":
        await store.undeprecate(item_id)
    else:  # deprecate
        reason: str | None = None
        try:
            body = await request.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and isinstance(body.get("reason"), str):
            reason = body["reason"] or None
        await store.deprecate(item_id, reason)

    updated = await store.get_item(item_id)
    assert updated is not None
    return JSONResponse(await _item_detail(qdrant, postgres, collection, updated))


async def api_items(request: Request) -> JSONResponse:
    """Items in one collection, filtered, sorted and paged. No user filter by
    default — admin view. Scans at most _SCAN_CAP items, filtering in memory."""
    storages = _storages(request)
    if storages is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)
    qdrant, _ = storages
    params = request.query_params

    collection = params.get("collection", "")
    if not await _valid_collection(qdrant, collection):
        return JSONResponse({"error": f"unknown collection {collection!r}"}, status_code=400)

    layer_raw = params.get("layer") or None
    layer = None
    if layer_raw:
        try:
            layer = MemoryLayer(layer_raw)
        except ValueError:
            return JSONResponse({"error": f"invalid layer {layer_raw!r}"}, status_code=400)

    include_deprecated = params.get("include_deprecated") == "true"
    user_filter = params.get("user") or None
    try:
        limit = min(int(params.get("limit", "50")), 200)
        offset = max(int(params.get("offset", "0")), 0)
    except ValueError:
        return JSONResponse({"error": "invalid limit or offset"}, status_code=400)

    sort_raw = params.get("sort") or "-created_at"
    descending = sort_raw.startswith("-")
    sort_field = sort_raw.lstrip("-")
    if sort_field not in _SORT_FIELDS:
        return JSONResponse({"error": f"invalid sort {sort_raw!r}"}, status_code=400)

    bounds: dict[str, datetime] = {}
    for key in ("from", "to"):
        if params.get(key):
            ts = _parse_ts(params[key])
            if ts is None:
                return JSONResponse({"error": f"invalid {key} {params[key]!r}"}, status_code=400)
            bounds[key] = ts

    q = (params.get("q") or "").lower()
    origin = params.get("origin") or None
    type_filter = params.get("type") or None
    stale_raw = params.get("stale")
    if stale_raw not in (None, "", "true", "false"):
        return JSONResponse({"error": f"invalid stale {stale_raw!r}"}, status_code=400)

    payloads, _next = await qdrant.scoped(collection).scan_all_items(
        memory_layer=layer,
        include_deprecated=include_deprecated,
        limit=_SCAN_CAP,
    )
    capped = len(payloads) >= _SCAN_CAP

    def keep(p: dict[str, Any]) -> bool:
        if user_filter and p.get("user_id") != user_filter:
            return False
        meta = _meta(p)
        if q and q not in (p.get("content") or "").lower():
            return False
        if origin and meta.get("origin") != origin:
            return False
        if type_filter and (meta.get("type") or p.get("type")) != type_filter:
            return False
        if bounds:
            created = _parse_ts(p.get("created_at") or "")
            if created is None:
                return False
            if "from" in bounds and created < bounds["from"]:
                return False
            if "to" in bounds and created > bounds["to"]:
                return False
        if stale_raw in ("true", "false"):
            if bool(_stale_files(p)) != (stale_raw == "true"):
                return False
        return True

    payloads = [p for p in payloads if keep(p)]

    def sort_key(p: dict[str, Any]) -> Any:
        if sort_field == "importance":
            return p.get("importance") or 0.0
        return p.get("created_at") or ""

    payloads.sort(key=sort_key, reverse=descending)
    total = len(payloads)

    items = []
    for p in payloads[offset:offset + limit]:
        items.append(_item_summary(p))

    return JSONResponse(
        {
            "collection": collection,
            "count": len(items),
            "total": total,
            "offset": offset,
            "capped": capped,
            "generated_at": datetime.now(UTC).isoformat(),
            "items": items,
        }
    )
