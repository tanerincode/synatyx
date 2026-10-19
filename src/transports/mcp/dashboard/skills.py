from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from src.models.skill import Skill
from src.transports.mcp.dashboard.common import (
    _iso,
    _storages,
)

logger = logging.getLogger(__name__)

# ── Skills ───────────────────────────────────────────────────────────────────
#
# The list is cross-user like the rest of the dashboard (an absent `user` means
# every user). Detail requires `user`: a slug is only unique inside one user's
# scope, so without it the lookup could return someone else's skill.

_SKILL_LIST_LIMIT = 100


def _skill_meta(s: Skill) -> dict[str, Any]:
    return {
        "slug": s.slug,
        "name": s.name,
        "description": s.description,
        "project": s.project,
        "user_id": s.user_id,
        "updated_at": _iso(s.updated_at),
    }


async def api_skills(request: Request) -> JSONResponse:
    """Skills with metadata only; the content comes from the detail route."""
    storages = _storages(request)
    if storages is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)
    _, postgres = storages

    skills = await postgres.skill_list_all(
        user_id=request.query_params.get("user") or None,
        project=request.query_params.get("project") or None,
        limit=_SKILL_LIST_LIMIT,
    )
    return JSONResponse({"skills": [_skill_meta(s) for s in skills]})


async def api_skill_detail(request: Request) -> JSONResponse:
    storages = _storages(request)
    if storages is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)
    _, postgres = storages

    user_id = request.query_params.get("user") or None
    if not user_id:
        return JSONResponse({"error": "user is required"}, status_code=400)

    skill = await postgres.skill_get_by_name(
        request.path_params["slug"], user_id, request.query_params.get("project") or None
    )
    if skill is None:
        return JSONResponse({"error": "skill not found"}, status_code=404)
    return JSONResponse(
        {**_skill_meta(skill), "content": skill.content, "frontmatter": skill.frontmatter}
    )
