from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

from src.models.task import TaskStatus
from src.transports.mcp.dashboard.common import (
    _storages,
)

logger = logging.getLogger(__name__)

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
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "description": t.description,
                    "status": t.status.value,
                    "priority": t.priority.value,
                    "project": t.project,
                    "user_id": t.user_id,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in tasks
            ],
        }
    )
