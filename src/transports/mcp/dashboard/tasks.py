from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from src.models.task import Task, TaskPriority, TaskStatus
from src.transports.mcp.dashboard.briefs import _json_body, _opt_str
from src.transports.mcp.dashboard.common import (
    _iso,
    _storages,
)

logger = logging.getLogger(__name__)


def _task_json(t: Task) -> dict[str, Any]:
    return {
        "id": t.id,
        "title": t.title,
        "description": t.description,
        "status": t.status.value,
        "priority": t.priority.value,
        "project": t.project,
        "assignee": t.assignee,
        "user_id": t.user_id,
        "created_at": _iso(t.created_at),
        "updated_at": _iso(t.updated_at),
    }


async def api_tasks(request: Request) -> JSONResponse:
    """Tasks across all users, optionally filtered by status."""
    storages = _storages(request)
    if storages is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)
    _, postgres = storages

    status_raw = request.query_params.get("status") or None
    status = None
    if status_raw and status_raw != "all":
        try:
            status = TaskStatus(status_raw)
        except ValueError:
            return JSONResponse({"error": f"invalid status {status_raw!r}"}, status_code=400)

    tasks = await postgres.task_list_all(status=status, limit=100)
    return JSONResponse(
        {
            "count": len(tasks),
            "tasks": [_task_json(t) for t in tasks],
        }
    )


async def api_task_update(request: Request) -> JSONResponse:
    """Edit one task. Every field is optional; the enums are validated first."""
    storages = _storages(request)
    if storages is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)
    _, postgres = storages

    body = await _json_body(request)
    if body is None:
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    # A task is only resolvable inside one user's scope (task_update checks the
    # owner), and the list route returns each task's user_id.
    user_id = _opt_str(body, "user") or request.query_params.get("user")
    if not user_id:
        return JSONResponse({"error": "user is required"}, status_code=400)

    fields: dict[str, Any] = {}
    for name, enum in (("status", TaskStatus), ("priority", TaskPriority)):
        if name in body:
            try:
                fields[name] = enum(body[name])
            except ValueError:
                return JSONResponse({"error": f"invalid {name} {body[name]!r}"}, status_code=400)
    for name in ("title", "description", "assignee"):
        if name in body:
            value = body[name]
            if not isinstance(value, str) or (name == "title" and not value.strip()):
                return JSONResponse({"error": f"invalid {name}"}, status_code=400)
            fields[name] = value

    updated = await postgres.task_update(request.path_params["id"], user_id, **fields)
    if updated is None:
        return JSONResponse({"error": "task not found"}, status_code=404)
    return JSONResponse(_task_json(updated))
