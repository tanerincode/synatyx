from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from src.core.handoff import HandoffService
from src.core.project import slugify
from src.models.handoff import Handoff, HandoffReport, HandoffStatus
from src.transports.mcp.dashboard.common import (
    _iso,
    _storages,
)

logger = logging.getLogger(__name__)

# ── Briefs (handoffs) ────────────────────────────────────────────────────────
#
# HandoffService is per user, the rest of the dashboard is cross-user. The list
# route takes an optional `user` (absent → every user); detail and mutations
# require it, because a brief id is only resolvable inside one user's scope.
# Auth is the admin-key middleware (Authorization: Bearer / X-Auth-Key header),
# never a cookie, so a cross-site form post cannot carry credentials.

_BRIEF_LIST_LIMIT = 100
_BRIEF_REPORT_LIMIT = 100


def _brief_meta(h: Handoff) -> dict[str, Any]:
    return {
        "id": h.id,
        "slug": h.slug,
        "title": h.title,
        "project": h.project,
        "assignee": h.assignee,
        "status": h.status.value,
        "version": h.version,
        "user_id": h.user_id,
        "created_at": _iso(h.created_at),
        "updated_at": _iso(h.updated_at),
    }


def _brief_status(raw: str | None) -> tuple[HandoffStatus | None, str | None]:
    """Parse a status param; returns (status, error)."""
    if not raw or raw == "all":
        return None, None
    try:
        return HandoffStatus(raw), None
    except ValueError:
        return None, f"invalid status {raw!r}"


async def _brief_detail(
    service: HandoffService, user_id: str, handoff_id: str, version: int | None = None
) -> dict[str, Any] | None:
    """The brief with its reports and version list.

    With `version`, `body` and `version` are that version's; everything else
    is current. Raises ValueError for a version that does not exist.
    """
    found = await service.get(
        user_id, handoff_id, version=version, report_limit=_BRIEF_REPORT_LIMIT
    )
    if found is None:
        return None
    h, reports = found
    versions = await service.versions(h.id)
    return {
        **_brief_meta(h),
        "body": h.body,
        "reports": [
            {
                "at": _iso(r.created_at),
                "agent": r.agent,
                "status": r.status_after.value if r.status_after else None,
                "text": r.text,
            }
            for r in reports
        ],
        "versions": [
            {"version": v["version"], "at": _iso(v["created_at"]), "author": v["author"]}
            for v in versions
        ],
    }


def _last_report(r: HandoffReport | None) -> dict[str, Any] | None:
    if r is None:
        return None
    return {"text": r.text, "agent": r.agent, "at": _iso(r.created_at)}


async def api_briefs(request: Request) -> JSONResponse:
    """Briefs (handoffs) with the newest report each; never the body."""
    storages = _storages(request)
    if storages is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)
    _, postgres = storages
    service = HandoffService(postgres)

    status, error = _brief_status(request.query_params.get("status"))
    if error:
        return JSONResponse({"error": error}, status_code=400)
    user_id = request.query_params.get("user") or None
    # Handoffs are stored under the slugified project (as the MCP tools do).
    raw_project = request.query_params.get("project")
    project = slugify(raw_project) if raw_project else None
    assignee = request.query_params.get("assignee") or None

    if user_id:
        handoffs = await service.list(
            user_id, project=project, assignee=assignee, status=status,
            limit=_BRIEF_LIST_LIMIT,
        )
    else:
        handoffs = await service.list_all(
            project=project, assignee=assignee, status=status, limit=_BRIEF_LIST_LIMIT
        )
    last = await service.last_reports([h.id for h in handoffs])
    briefs = [
        {
            "id": h.id,
            "title": h.title,
            "project": h.project,
            "assignee": h.assignee,
            "status": h.status.value,
            "version": h.version,
            "updated_at": _iso(h.updated_at),
            "last_report": _last_report(last.get(h.id)),
            "slug": h.slug,
            "user_id": h.user_id,
        }
        for h in handoffs
    ]
    return JSONResponse({"briefs": briefs})


async def api_brief_detail(request: Request) -> JSONResponse:
    storages = _storages(request)
    if storages is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)
    _, postgres = storages
    user_id = request.query_params.get("user") or None
    if not user_id:
        return JSONResponse({"error": "user is required"}, status_code=400)
    version: int | None = None
    raw_version = request.query_params.get("version")
    if raw_version is not None:
        try:
            version = int(raw_version)
        except ValueError:
            return JSONResponse({"error": f"invalid version {raw_version!r}"}, status_code=400)
    try:
        detail = await _brief_detail(
            HandoffService(postgres), user_id, request.path_params["id"], version
        )
    except ValueError:
        return JSONResponse({"error": f"version {version} not found"}, status_code=404)
    if detail is None:
        return JSONResponse({"error": "brief not found"}, status_code=404)
    return JSONResponse(detail)


async def _json_body(request: Request) -> dict[str, Any] | None:
    try:
        body = await request.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _opt_str(body: dict[str, Any], key: str) -> str | None:
    value = body.get(key)
    return value if isinstance(value, str) else None


async def api_brief_create(request: Request) -> JSONResponse:
    storages = _storages(request)
    if storages is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)
    _, postgres = storages
    body = await _json_body(request)
    if body is None:
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    user_id = _opt_str(body, "user") or request.query_params.get("user")
    if not user_id:
        return JSONResponse({"error": "user is required"}, status_code=400)
    project, title = _opt_str(body, "project"), _opt_str(body, "title")
    if not project or not title:
        return JSONResponse({"error": "project and title are required"}, status_code=400)
    status, error = _brief_status(_opt_str(body, "status"))
    if error:
        return JSONResponse({"error": error}, status_code=400)

    service = HandoffService(postgres)
    try:
        created = await service.create(
            user_id,
            slugify(project),
            title,
            _opt_str(body, "body") or "",
            assignee=_opt_str(body, "assignee") or None,
            slug=_opt_str(body, "slug") or None,
            status=status,
            created_by=_opt_str(body, "author") or "dashboard",
        )
    except ValueError as exc:
        code = 409 if "already exists" in str(exc) else 400
        return JSONResponse({"error": str(exc)}, status_code=code)
    detail = await _brief_detail(service, user_id, created.id)
    return JSONResponse(detail, status_code=201)


async def api_brief_update(request: Request) -> JSONResponse:
    storages = _storages(request)
    if storages is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)
    _, postgres = storages
    body = await _json_body(request)
    if body is None:
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    user_id = _opt_str(body, "user") or request.query_params.get("user")
    if not user_id:
        return JSONResponse({"error": "user is required"}, status_code=400)
    status, error = _brief_status(_opt_str(body, "status"))
    if error:
        return JSONResponse({"error": error}, status_code=400)

    service = HandoffService(postgres)
    handoff_id = request.path_params["id"]
    updated = await service.update(
        user_id,
        handoff_id,
        body=_opt_str(body, "body"),
        title=_opt_str(body, "title"),
        status=status,
        assignee=_opt_str(body, "assignee"),
        author=_opt_str(body, "author") or "dashboard",
    )
    if updated is None:
        return JSONResponse({"error": "brief not found"}, status_code=404)
    return JSONResponse(await _brief_detail(service, user_id, handoff_id))
