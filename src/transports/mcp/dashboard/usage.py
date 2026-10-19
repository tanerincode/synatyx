from __future__ import annotations

import logging
from datetime import UTC, datetime

from starlette.requests import Request
from starlette.responses import JSONResponse

from src.transports.mcp.dashboard.common import (
    _storages,
)

logger = logging.getLogger(__name__)

async def api_usage(request: Request) -> JSONResponse:
    """Token spend: totals plus per-tool, per-project, and per-day breakdowns."""
    storages = _storages(request)
    if storages is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)
    _, postgres = storages

    try:
        days = max(1, min(int(request.query_params.get("days", 30)), 365))
    except ValueError:
        return JSONResponse({"error": "days must be an integer"}, status_code=400)
    project = request.query_params.get("project") or None
    user_id = request.query_params.get("user") or None

    from datetime import timedelta

    from src.config import settings

    since = datetime.now(UTC) - timedelta(days=days)
    try:
        totals = await postgres.usage_totals(user_id=user_id, project=project, since=since)
        by_tool = await postgres.usage_stats(
            user_id=user_id, project=project, since=since, group_by="tool")
        by_project = await postgres.usage_stats(
            user_id=user_id, project=project, since=since, group_by="project")
        by_day = await postgres.usage_stats(
            user_id=user_id, project=project, since=since, group_by="day")
    except Exception:
        logger.exception("usage query failed")
        return JSONResponse({"error": "usage query failed"}, status_code=500)

    totals["embedding_cost_usd"] = round(
        totals["embedding_tokens"] / 1_000_000 * settings.usage.embedding_price_per_mtok, 6
    )
    return JSONResponse({
        "days": days,
        "project": project,
        "user": user_id,
        "totals": totals,
        "by_tool": by_tool,
        "by_project": by_project,
        "by_day": by_day,
    })
