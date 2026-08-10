from __future__ import annotations

import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from src.config import settings
from src.storage.postgres import PostgresStorage
from src.storage.qdrant import QdrantStorage
from src.storage.redis import RedisStorage
from src.transports.mcp.dashboard import (
    api_graph,
    api_index_chunks,
    api_index_graph,
    api_indexes,
    api_items,
    api_overview,
    api_tasks,
    api_usage,
    api_users,
    dashboard_page,
)
from src.transports.mcp.server import SynatyxMCPServer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Admin-key auth middleware (pure ASGI so SSE streaming is untouched).
# Validates a static key from the env against an incoming header. The key may
# be sent either in the configured header (default X-Auth-Key) or as
# `Authorization: Bearer <key>`. Public paths (e.g. /health) bypass the check.
# ---------------------------------------------------------------------------

class AdminKeyAuthMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        admin_key: str,
        header_name: str,
        public_paths: frozenset[str],
    ) -> None:
        self.app = app
        self._admin_key = admin_key.encode()
        self._header_name = header_name.strip().lower().encode()
        self._public_paths = public_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._public_paths:
            await self.app(scope, receive, send)
            return

        if self._is_authorized(scope):
            await self.app(scope, receive, send)
            return

        response = JSONResponse({"error": "unauthorized"}, status_code=401)
        await response(scope, receive, send)

    def _is_authorized(self, scope: Scope) -> bool:
        headers = dict(scope.get("headers") or [])

        provided = headers.get(self._header_name)
        if provided is None:
            auth = headers.get(b"authorization", b"")
            if auth[:7].lower() == b"bearer ":
                provided = auth[7:].strip()

        return provided is not None and hmac.compare_digest(provided, self._admin_key)

# ---------------------------------------------------------------------------
# FastMCP instance — host/port resolved from env so Docker can override them.
# ---------------------------------------------------------------------------

_host = os.getenv("HOST", "0.0.0.0")
_port = int(os.getenv("PORT", "9000"))

mcp = FastMCP(
    "synatyx-context-engine",
    host=_host,
    port=_port,
    sse_path="/mcp/sse",
    message_path="/mcp/messages/",
    streamable_http_path="/mcp",
    # Every Synatyx tool is stateless per-call, so no session state to lose:
    # deploy restarts stop stranding clients on dead session ids (the old
    # SSE -32602 problem).
    stateless_http=True,
)


# ---------------------------------------------------------------------------
# Lifespan — connect to all storage backends once, inject into FastMCP.
# SynatyxMCPServer registers every tool handler on the low-level mcp.Server.
# We swap FastMCP's internal server so the SSE transport carries the full
# tool set without re-registering anything.
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app: Starlette) -> AsyncIterator[None]:
    qdrant = QdrantStorage(
        host=settings.qdrant.host,
        port=settings.qdrant.port,
        collection_name=settings.qdrant.collection_name,
    )
    await qdrant.init_collection()

    redis = RedisStorage(url=settings.redis.url)
    await redis.ping()

    postgres = PostgresStorage(dsn=settings.postgres.dsn)
    await postgres.connect()

    synatyx = SynatyxMCPServer(qdrant, redis, postgres)
    # Inject the fully-wired low-level Server into FastMCP so that handle_sse
    # picks it up on every incoming request. The streamable-HTTP session
    # manager captured the placeholder server at construction, so rebind its
    # app reference too — it reads self.app per request.
    mcp._mcp_server = synatyx._server
    mcp.session_manager.app = synatyx._server
    # Expose the server to plain REST routes (e.g. /capture) via app state.
    _app.state.synatyx = synatyx
    # Raw storages for the dashboard API (read-only aggregate views).
    _app.state.qdrant = qdrant
    _app.state.postgres = postgres

    # Background compaction of idle session traces (implicit capture)
    import asyncio
    tracking_task = asyncio.create_task(synatyx.run_tracking_loop())

    logger.info(
        "Synatyx MCP HTTP server ready on %s:%d — streamable-HTTP at /mcp, "
        "legacy SSE at /mcp/sse",
        _host, _port,
    )

    # The streamable-HTTP session manager needs its task group running for
    # the lifetime of the app.
    async with mcp.session_manager.run():
        yield

    tracking_task.cancel()
    await qdrant.close()
    await redis.close()
    await postgres.close()


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

def server_info() -> dict:
    """Build/runtime identity shown on /health and the dashboard — makes it
    obvious at a glance which build (local vs prod) a server is running."""
    from src.transports.mcp.tools import TOOL_DEFINITIONS
    return {
        "version": settings.app_version,
        "commit": settings.git_commit,
        "tools": len(TOOL_DEFINITIONS),
        "transports": ["streamable-http:/mcp", "sse:/mcp/sse (deprecated)"],
    }


async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "synatyx-mcp", **server_info()})


# ---------------------------------------------------------------------------
# Capture endpoint — automatic memory capture from outside the MCP loop.
# Session-end hooks (Claude Code / Cursor), CI jobs, or cron scripts POST a
# digest here so memory writes stop depending on agent discipline. Protected
# by the admin-key middleware like every non-public path.
# ---------------------------------------------------------------------------

async def capture(request: Request) -> JSONResponse:
    synatyx = getattr(request.app.state, "synatyx", None)
    if synatyx is None:
        return JSONResponse({"error": "server not ready"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    user_id = str(body.get("user_id") or "").strip()
    content = str(body.get("content") or "").strip()
    if not user_id or not content:
        return JSONResponse({"error": "user_id and content are required"}, status_code=400)

    try:
        result = await synatyx.capture(
            user_id=user_id,
            content=content,
            session_id=body.get("session_id"),
            project=body.get("project"),
            memory_layer=body.get("memory_layer", "L2"),
            importance=float(body.get("importance", 0.6)),
            metadata=body.get("metadata"),
            origin=body.get("origin"),
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception:
        logger.exception("Capture failed")
        return JSONResponse({"error": "capture failed"}, status_code=500)

    return JSONResponse({"status": "captured", **result})


# ---------------------------------------------------------------------------
# Push-indexing endpoints — automatic project indexing from machines the
# server can't read (client diffs file hashes, uploads only what changed).
# Used by scripts/index_project.py; protected by the admin-key middleware.
# ---------------------------------------------------------------------------

async def _index_service_from(request: Request):
    synatyx = getattr(request.app.state, "synatyx", None)
    if synatyx is None:
        return None, JSONResponse({"error": "server not ready"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return None, JSONResponse({"error": "invalid JSON body"}, status_code=400)
    user_id = str(body.get("user_id") or "").strip()
    project = str(body.get("project") or "").strip()
    if not user_id or not project:
        return None, JSONResponse(
            {"error": "user_id and project are required"}, status_code=400
        )
    from src.core.project import slugify
    svc, _ = await synatyx._get_index_services(user_id, slugify(project))
    return (svc, user_id, body), None


async def index_script(_request: Request):
    """Serve scripts/index_project.py so clients can self-update:
    curl -s -H "X-Auth-Key: $KEY" $URL/index/script | python3 - --root .
    Developers never need the Synatyx repo — the script version always
    matches the server it talks to."""
    from starlette.responses import PlainTextResponse
    script = Path(__file__).resolve().parents[3] / "scripts" / "index_project.py"
    try:
        return PlainTextResponse(script.read_text(encoding="utf-8"))
    except OSError:
        return JSONResponse({"error": "script not available"}, status_code=404)


async def index_diff(request: Request) -> JSONResponse:
    ok, err = await _index_service_from(request)
    if err is not None:
        return err
    svc, user_id, body = ok
    files = body.get("files")
    if not isinstance(files, dict):
        return JSONResponse({"error": "files must be a {path: hash} object"}, status_code=400)
    try:
        return JSONResponse(await svc.diff_files(user_id, files))
    except Exception:
        logger.exception("index diff failed")
        return JSONResponse({"error": "diff failed"}, status_code=500)


async def index_files(request: Request) -> JSONResponse:
    ok, err = await _index_service_from(request)
    if err is not None:
        return err
    svc, user_id, body = ok
    files = body.get("files") or []
    prune = body.get("prune") or []
    if not isinstance(files, list) or not isinstance(prune, list):
        return JSONResponse(
            {"error": "files must be a list of {path, content}; prune a list of paths"},
            status_code=400,
        )
    # Index pushes embed outside the MCP tool loop — meter them like a tool
    # call so extension-driven indexing shows up in token spend too.
    from src.core.budget import estimate_tokens
    from src.core.usage import usage_begin, usage_end

    usage_begin()
    failed = False
    try:
        result = await svc.index_content(user_id, files, force=bool(body.get("force")))
        pruned = await svc.remove_files(user_id, [str(p) for p in prune]) if prune else 0
        response: JSONResponse = JSONResponse({**result.to_dict(), "chunks_pruned": pruned})
    except Exception:
        logger.exception("index upload failed")
        failed = True
        response = JSONResponse({"error": "index failed"}, status_code=500)
    await request.app.state.synatyx._usage.record(
        user_id=user_id,
        tool="index_push",
        input_tokens=estimate_tokens("".join(str(f.get("content", "")) for f in files if isinstance(f, dict))),
        output_tokens=0,
        embedding_tokens=usage_end(),
        project=body.get("project") or None,
        error=failed,
    )
    return response


# ---------------------------------------------------------------------------
# ASGI app — streamable-HTTP (modern, /mcp) + legacy SSE (deprecated,
# /mcp/sse) + /health + /capture + dashboard, wrapped with lifespan.
# ---------------------------------------------------------------------------

# streamable_http_app() must be built before the lifespan runs — it creates
# mcp.session_manager, which the lifespan rebinds and runs.
_streamable_app = mcp.streamable_http_app()
_sse_app = mcp.sse_app()

# /dashboard serves only the static shell — every data endpoint under
# /dashboard/api/* stays behind the admin-key middleware.
_PUBLIC_PATHS = frozenset({"/health", "/dashboard"})

_middleware = []
if settings.auth.enabled:
    _middleware.append(
        Middleware(
            AdminKeyAuthMiddleware,
            admin_key=settings.auth.admin_key,
            header_name=settings.auth.header_name,
            public_paths=_PUBLIC_PATHS,
        )
    )
    logger.info("Admin-key auth enabled — expecting key in '%s' header", settings.auth.header_name)
else:
    logger.warning("AUTH_ADMIN_KEY not set — MCP HTTP server is UNAUTHENTICATED")

app = Starlette(
    routes=_streamable_app.routes + _sse_app.routes + [
        Route("/health", health),
        Route("/capture", capture, methods=["POST"]),
        Route("/index/diff", index_diff, methods=["POST"]),
        Route("/index/files", index_files, methods=["POST"]),
        Route("/index/script", index_script),
        Route("/dashboard", dashboard_page),
        Route("/dashboard/api/overview", api_overview),
        Route("/dashboard/api/items", api_items),
        Route("/dashboard/api/tasks", api_tasks),
        Route("/dashboard/api/users", api_users),
        Route("/dashboard/api/graph", api_graph),
        Route("/dashboard/api/indexes", api_indexes),
        Route("/dashboard/api/index_graph", api_index_graph),
        Route("/dashboard/api/index_chunks", api_index_chunks),
        Route("/dashboard/api/usage", api_usage),
    ],
    middleware=_middleware,
    lifespan=lifespan,
)

