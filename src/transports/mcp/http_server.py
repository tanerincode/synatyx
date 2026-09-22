from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import ProviderTokenVerifier, TokenVerifier
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.authentication import AuthCredentials
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.config import settings
from src.core.oauth import SynatyxOAuthProvider
from src.core.scoped_keys import (
    AdminScope,
    ScopedKey,
    authorize_request,
    resolve_scope,
)
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
from src.transports.mcp.oauth import (
    LOGIN_PATH,
    PUBLIC_OAUTH_PATHS,
    PUBLIC_OAUTH_PREFIXES,
    build_auth_settings,
    build_oauth_routes,
    resource_metadata_url,
)
from src.transports.mcp.server import SynatyxMCPServer
from src.transports.rest.v1 import routes as v1_routes

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth middleware (pure ASGI so SSE streaming is untouched). A request passes
# if it carries EITHER the static admin key — in the configured header
# (default X-Auth-Key) or as `Authorization: Bearer <key>` — OR a valid OAuth
# 2.1 access token issued by the built-in authorization server (verified
# through the MCP SDK's TokenVerifier). Public paths (/health, the OAuth
# endpoints, /.well-known/*) bypass the check entirely.
# ---------------------------------------------------------------------------

async def _buffered_receive(scope: Scope, receive: Receive) -> tuple[bytes | None, Receive]:
    """Read a request body so it can be inspected, and hand back a receive that replays it.

    Only POST bodies are read. A GET carries nothing to inspect, and draining
    one would stall the SSE transport, which holds its request open by design.
    """
    if str(scope.get("method") or "").upper() != "POST":
        return None, receive

    messages: list[Message] = []
    body = b""
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request":
            break
        body += message.get("body", b"")
        if not message.get("more_body", False):
            break

    async def replay() -> Message:
        if messages:
            return messages.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    return body, replay


class AdminKeyAuthMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        admin_key: str,
        header_name: str,
        public_paths: frozenset[str],
        public_prefixes: tuple[str, ...] = (),
        token_verifier: TokenVerifier | None = None,
        resource_metadata_url: str | None = None,
        scoped_keys: list[ScopedKey] | None = None,
    ) -> None:
        self.app = app
        self._admin_key = admin_key.encode()
        self._header_name = header_name.strip().lower().encode()
        self._public_paths = public_paths
        self._public_prefixes = public_prefixes
        self._token_verifier = token_verifier
        self._resource_metadata_url = resource_metadata_url
        self._scoped_keys = scoped_keys or []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path") or "")
        if scope["type"] != "http" or self._is_public(path):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        key_scope = self._resolve_scope(headers)

        # An OAuth token is issued only to whoever proved they hold the owner
        # secret, so it carries the owner's reach.
        if key_scope is None and await self._authorize_token(scope, headers):
            key_scope = AdminScope()

        if key_scope is None:
            response = JSONResponse(
                {"error": "unauthorized"}, status_code=401, headers=self._challenge_headers()
            )
            await response(scope, receive, send)
            return

        if not key_scope.is_admin:
            # What a scoped key is allowed to do depends on the tool and
            # project named in the body, so the body has to be read here and
            # replayed to the application afterwards. Every MCP call is a POST
            # to one path; a path-only check cannot tell them apart.
            body, receive = await _buffered_receive(scope, receive)
            denial = authorize_request(key_scope, path, body)
            if denial is not None:
                logger.warning("Refused a scoped key: %s", denial)
                response = JSONResponse(
                    {"error": "forbidden", "detail": denial}, status_code=403
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)

    def _is_public(self, path: str) -> bool:
        return path in self._public_paths or path.startswith(self._public_prefixes)

    def _resolve_scope(self, headers: dict[bytes, bytes]) -> AdminScope | ScopedKey | None:
        provided = headers.get(self._header_name)
        if provided is None:
            provided = self._bearer_token(headers)

        return resolve_scope(provided, self._admin_key.decode(), self._scoped_keys)

    @staticmethod
    def _bearer_token(headers: dict[bytes, bytes]) -> bytes | None:
        auth = headers.get(b"authorization", b"")
        if auth[:7].lower() == b"bearer ":
            return auth[7:].strip()
        return None

    async def _authorize_token(self, scope: Scope, headers: dict[bytes, bytes]) -> bool:
        """Validate an OAuth access token and publish the principal on the scope."""
        if self._token_verifier is None:
            return False
        raw = self._bearer_token(headers)
        if not raw:
            return False
        try:
            access_token = await self._token_verifier.verify_token(raw.decode("latin-1"))
        except Exception:  # pragma: no cover - storage hiccup must not 500
            logger.exception("OAuth token verification failed")
            return False
        if access_token is None:
            return False
        if access_token.expires_at is not None and access_token.expires_at < int(time.time()):
            return False
        # Same shape the SDK's BearerAuthBackend produces, so anything
        # downstream reading scope["user"]/["auth"] behaves identically.
        scope["user"] = AuthenticatedUser(access_token)
        scope["auth"] = AuthCredentials(access_token.scopes)
        return True

    def _challenge_headers(self) -> dict[str, str]:
        """RFC 9728 §5.1: point the client at the protected-resource metadata so
        it can discover the authorization server and start the OAuth flow."""
        if not self._resource_metadata_url:
            return {}
        return {"WWW-Authenticate": f'Bearer resource_metadata="{self._resource_metadata_url}"'}

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

    # The OAuth routes are built at import time (before any connection exists),
    # so hand the provider its storage backends now: clients live in Postgres,
    # codes and tokens in Redis.
    if _oauth_provider is not None:
        _oauth_provider.bind(clients=postgres, kv=redis)

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

# ---------------------------------------------------------------------------
# OAuth 2.1 authorization server — for clients that cannot send a static
# header (claude.ai custom connectors). Only wired up when the admin key is
# set: it doubles as the owner secret on the authorize page, and an
# authorization server with no owner secret would hand memories to anyone.
# When AUTH_ADMIN_KEY is empty nothing below runs and behaviour is unchanged.
# ---------------------------------------------------------------------------

_public_url = settings.public_url.rstrip("/")
_oauth_provider: SynatyxOAuthProvider | None = None
_oauth_routes: list[Route] = []
_token_verifier: TokenVerifier | None = None
_resource_metadata_url: str | None = None

if settings.auth.enabled and settings.oauth.enabled:
    _oauth_provider = SynatyxOAuthProvider(
        public_url=_public_url,
        owner_secrets=[settings.auth.admin_key, settings.oauth.owner_password],
        scopes=[settings.oauth.scope],
        login_path=LOGIN_PATH,
        code_ttl_seconds=settings.oauth.code_ttl_seconds,
        access_token_ttl_seconds=settings.oauth.access_token_ttl_seconds,
        refresh_token_ttl_seconds=settings.oauth.refresh_token_ttl_seconds,
        client_secret_ttl_seconds=settings.oauth.client_secret_ttl_seconds,
        unused_client_ttl_seconds=settings.oauth.unused_client_ttl_seconds,
        max_clients=settings.oauth.max_clients,
        login_max_failures=settings.oauth.login_max_failures,
        login_max_per_minute=settings.oauth.login_max_per_minute,
    )
    try:
        _oauth_routes = build_oauth_routes(
            _oauth_provider, build_auth_settings(_public_url, [settings.oauth.scope])
        )
    except ValueError as exc:
        # The SDK rejects a non-HTTPS, non-localhost issuer (RFC 8414). Degrade
        # to admin-key-only rather than refusing to boot.
        logger.error("OAuth disabled — invalid PUBLIC_URL %r: %s", settings.public_url, exc)
        _oauth_provider = None
        _oauth_routes = []
    else:
        _token_verifier = ProviderTokenVerifier(_oauth_provider)
        _resource_metadata_url = resource_metadata_url(_public_url)
        logger.info("OAuth 2.1 authorization server enabled — issuer %s", _public_url)

_middleware = []
if settings.auth.enabled:
    _middleware.append(
        Middleware(
            AdminKeyAuthMiddleware,
            admin_key=settings.auth.admin_key,
            header_name=settings.auth.header_name,
            public_paths=(_PUBLIC_PATHS | PUBLIC_OAUTH_PATHS) if _oauth_routes else _PUBLIC_PATHS,
            scoped_keys=settings.auth.scoped_key_list,
            public_prefixes=PUBLIC_OAUTH_PREFIXES if _oauth_routes else (),
            token_verifier=_token_verifier,
            resource_metadata_url=_resource_metadata_url,
        )
    )
    logger.info("Admin-key auth enabled — expecting key in '%s' header", settings.auth.header_name)
else:
    logger.warning("AUTH_ADMIN_KEY not set — MCP HTTP server is UNAUTHENTICATED")

app = Starlette(
    routes=_streamable_app.routes + _sse_app.routes + _oauth_routes + v1_routes + [
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

