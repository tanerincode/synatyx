from __future__ import annotations

import functools
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from src.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Why this transport exists
#
# MCP is a good protocol for an agent and a poor one for a backend service. A
# NestJS service calling Synatyx over MCP has to speak JSON-RPC, manage a
# session and unwrap tool results out of TextContent — all to make what is, on
# its side, one HTTP call. These routes are that call.
#
# They are a transport, not a second implementation: every route delegates to
# the same tools and services the MCP transport uses, through
# SynatyxMCPServer, so behaviour, metering and session capture cannot drift
# between the two.
# ---------------------------------------------------------------------------

# Error codes. A caller branches on these, never on the prose, and the set is
# part of the contract — the difference between "this project has nothing
# ingested yet" and "Synatyx is broken" decides whether the caller degrades
# gracefully or pages someone.
INVALID_REQUEST = "INVALID_REQUEST"
NOT_READY = "NOT_READY"
UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
INTERNAL_ERROR = "INTERNAL_ERROR"

DEFAULT_TOP_K = 8

# How long a health probe's backend check is reused. A readiness probe runs
# every few seconds per instance; without this, "is the vector store alive"
# turns into steady background load whose only purpose is answering a question
# whose answer almost never changes.
_HEALTH_CACHE_SECONDS = 5.0
_health_cache: tuple[float, dict[str, Any]] | None = None


def error(
    code: str, message: str, status: int, details: Any = None
) -> JSONResponse:
    """Every non-2xx answer, in one shape."""
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status)


def _field(body: dict[str, Any], *names: str, default: Any = None) -> Any:
    """First present, non-null value among `names`.

    Routes are documented in camelCase, which is what the calling service
    writes, but the MCP tools have always used snake_case and some callers
    port straight across from them. Accepting both on input costs one lookup
    and saves a class of 400 that teaches the caller nothing.
    """
    for name in names:
        value = body.get(name)
        if value is not None:
            return value
    return default


def _text(body: dict[str, Any], *names: str) -> str:
    value = _field(body, *names)
    return str(value).strip() if value is not None else ""


async def _parse(request: Request) -> tuple[Any, dict[str, Any] | None, JSONResponse | None]:
    """The three things every route needs: the server, the body, or an error."""
    synatyx = getattr(request.app.state, "synatyx", None)
    if synatyx is None:
        return None, None, error(NOT_READY, "Server is still starting", 503)

    try:
        body = await request.json()
    except Exception:
        return None, None, error(INVALID_REQUEST, "Body must be valid JSON", 400)

    if not isinstance(body, dict):
        return None, None, error(INVALID_REQUEST, "Body must be a JSON object", 400)

    return synatyx, body, None


def _require(
    body: dict[str, Any], fields: dict[str, tuple[str, ...]]
) -> tuple[dict[str, str], JSONResponse | None]:
    """Pull required string fields, naming every missing one at once.

    A caller fixing one missing field at a time per round trip is a caller
    reading a bad error message.
    """
    values: dict[str, str] = {}
    missing: list[str] = []
    for label, names in fields.items():
        value = _text(body, *names)
        if value:
            values[label] = value
        else:
            missing.append(label)

    if missing:
        return {}, error(
            INVALID_REQUEST,
            f"Missing required field(s): {', '.join(missing)}",
            400,
            {"missing": missing},
        )
    return values, None


def _user_id(body: dict[str, Any]) -> str:
    """Who the items belong to.

    Knowledge routes do not carry a user id — the knowledge is the tenant's,
    not a person's — so they fall back to the server's configured default.
    Scoped API keys will make this the key's identity rather than a body field
    or a global default; until then the fallback keeps single-tenant
    deployments working without ceremony.
    """
    return _text(body, "userId", "user_id") or settings.default_user_id


def _citation_url(metadata: dict[str, Any]) -> str | None:
    """A URL a caller can actually link to, or nothing.

    `source` holds a URL for crawled documents and a file path or a caller's
    own identifier for everything else. Returning a path as a citation link
    produces a dead link in someone's UI, so only real URLs come back.
    """
    for key in ("url", "source"):
        value = metadata.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return None


def _to_chunk(item: dict[str, Any]) -> dict[str, Any]:
    """One retrieved item as a citable chunk.

    Citation fields come back as explicit nulls rather than absent keys: a
    caller rendering citations needs to distinguish "no title" from "this
    server does not send titles", and only one of those is worth a bug report.
    """
    metadata = item.get("metadata") or {}
    start = metadata.get("offset_start")
    end = metadata.get("offset_end")

    return {
        "chunkId": item.get("id"),
        "sourceId": metadata.get("source_id"),
        "text": item.get("content", ""),
        "score": item.get("score"),
        "url": _citation_url(metadata),
        "title": metadata.get("title") or metadata.get("section"),
        "offsets": (
            {"start": start, "end": end}
            if isinstance(start, int) and isinstance(end, int)
            else None
        ),
    }


# Exceptions a tool raises because of what the caller sent, not because
# anything is broken. They must not come back as 5xx: a consumer rightly reads
# any 5xx as "this dependency is down" and pages someone, and a mistyped
# memoryLayer is not worth waking anyone for.
_CLIENT_ERROR_TYPES = frozenset({"ValueError", "KeyError", "TypeError", "ValidationError"})


def _tool_failed(result: dict[str, Any]) -> JSONResponse | None:
    """Tools report failure in their result rather than by raising."""
    if "error" not in result:
        return None

    message = str(result.get("error"))
    if str(result.get("error_type") or "") in _CLIENT_ERROR_TYPES:
        return error(INVALID_REQUEST, message, 400, {"tool": result.get("tool")})

    logger.error("Tool %r failed: %s", result.get("tool"), message)
    return error(UPSTREAM_UNAVAILABLE, message, 502, {"tool": result.get("tool")})


_Handler = Callable[[Request], Awaitable[JSONResponse]]


def _enveloped(handler: _Handler) -> _Handler:
    """Guarantee that every answer from this API is an envelope.

    A consumer that sees a 5xx whose body is not an envelope has to assume the
    worst — a crashing proxy answers with HTML — so an unhandled exception
    escaping a route would be indistinguishable from the server being gone.
    """

    @functools.wraps(handler)
    async def wrapper(request: Request) -> JSONResponse:
        try:
            return await handler(request)
        except Exception:
            logger.exception("Unhandled error in %s", handler.__name__)
            return error(INTERNAL_ERROR, "Unhandled server error", 500)

    return wrapper


# ---------------------------------------------------------------------------
# Knowledge
# ---------------------------------------------------------------------------

async def ingest(request: Request) -> JSONResponse:
    """Ingest one document, from a URL this server fetches or text the caller pushes."""
    synatyx, body, err = await _parse(request)
    if err is not None:
        return err
    assert body is not None

    values, err = _require(body, {"project": ("project",), "sourceId": ("sourceId", "source_id")})
    if err is not None:
        return err

    url = _text(body, "url") or None
    text = _field(body, "text")
    text = str(text) if text is not None else None

    if (url is None) == (text is None):
        return error(
            INVALID_REQUEST, "Provide exactly one of url or text", 400,
            {"url": url is not None, "text": text is not None},
        )

    metadata = _field(body, "metadata", default={})
    if not isinstance(metadata, dict):
        return error(INVALID_REQUEST, "metadata must be an object", 400)

    try:
        result = await synatyx.ingest_source(
            user_id=_user_id(body),
            project=values["project"],
            source_id=values["sourceId"],
            url=url,
            text=text,
            metadata=metadata,
            session_id=_text(body, "sessionId", "session_id") or None,
        )
    except ValueError as exc:
        return error(INVALID_REQUEST, str(exc), 400)
    except Exception:
        logger.exception("Ingest failed for source %r", values["sourceId"])
        return error(UPSTREAM_UNAVAILABLE, "Ingest failed", 502)

    return JSONResponse({
        "sourceId": result["source_id"],
        "source": result["source"],
        "chunkCount": result["chunks_stored"],
        "chunksFailed": result["chunks_failed"],
    })


async def retrieve(request: Request) -> JSONResponse:
    """Hybrid retrieval over one project's knowledge, as citable chunks."""
    synatyx, body, err = await _parse(request)
    if err is not None:
        return err
    assert body is not None

    values, err = _require(body, {"project": ("project",), "query": ("query",)})
    if err is not None:
        return err

    try:
        top_k = int(_field(body, "topK", "top_k", default=DEFAULT_TOP_K))
    except (TypeError, ValueError):
        return error(INVALID_REQUEST, "topK must be an integer", 400)
    if top_k < 1:
        return error(INVALID_REQUEST, "topK must be at least 1", 400)

    result = await synatyx.run_tool("context_retrieve", {
        "query": values["query"],
        "user_id": _user_id(body),
        "project": values["project"],
        "top_k": top_k,
        "memory_layers": ["L3"],
    })
    failure = _tool_failed(result)
    if failure is not None:
        return failure

    chunks = [_to_chunk(item) for item in result.get("context_items", [])]

    # An empty knowledge base is a normal state, not a failure: a caller that
    # has ingested nothing yet, or whose filters matched nothing, gets an
    # empty list and decides for itself. The diagnostics the retrieve service
    # already produces ride along, because "why was this empty" is otherwise
    # unanswerable from the outside.
    response: dict[str, Any] = {"chunks": chunks}
    if not chunks and "diagnostics" in result:
        response["diagnostics"] = result["diagnostics"]
    return JSONResponse(response)


async def deprecate_source(request: Request) -> JSONResponse:
    """Retire every chunk that came from one source id."""
    synatyx, body, err = await _parse(request)
    if err is not None:
        return err
    assert body is not None

    values, err = _require(body, {"project": ("project",), "sourceId": ("sourceId", "source_id")})
    if err is not None:
        return err

    try:
        result = await synatyx.deprecate_source(
            user_id=_user_id(body),
            project=values["project"],
            source_id=values["sourceId"],
            reason=_text(body, "reason") or None,
        )
    except Exception:
        logger.exception("Deprecate failed for source %r", values["sourceId"])
        return error(UPSTREAM_UNAVAILABLE, "Deprecate failed", 502)

    # Zero is a successful no-op, not a 404: a sync that retires an already
    # retired source has done its job, and a caller retrying after a timeout
    # should not have to treat the second attempt as an error.
    return JSONResponse({"sourceId": result["source_id"], "deprecated": result["deprecated"]})


# ---------------------------------------------------------------------------
# Conversation memory
# ---------------------------------------------------------------------------

async def memory_store(request: Request) -> JSONResponse:
    synatyx, body, err = await _parse(request)
    if err is not None:
        return err
    assert body is not None

    values, err = _require(body, {
        "project": ("project",),
        "userId": ("userId", "user_id"),
        "content": ("content",),
    })
    if err is not None:
        return err

    metadata = _field(body, "metadata", default={})
    if not isinstance(metadata, dict):
        return error(INVALID_REQUEST, "metadata must be an object", 400)

    result = await synatyx.run_tool("context_store", {
        "content": values["content"],
        "user_id": values["userId"],
        "project": values["project"],
        "memory_layer": _text(body, "memoryLayer", "memory_layer") or "L2",
        "metadata": metadata,
        "session_id": _text(body, "conversationId", "session_id") or None,
    })
    failure = _tool_failed(result)
    if failure is not None:
        return failure

    return JSONResponse({"id": result.get("item_id"), "ids": result.get("item_ids", [])})


async def memory_retrieve(request: Request) -> JSONResponse:
    synatyx, body, err = await _parse(request)
    if err is not None:
        return err
    assert body is not None

    values, err = _require(body, {
        "project": ("project",),
        "userId": ("userId", "user_id"),
        "query": ("query",),
    })
    if err is not None:
        return err

    try:
        top_k = int(_field(body, "topK", "top_k", default=DEFAULT_TOP_K))
    except (TypeError, ValueError):
        return error(INVALID_REQUEST, "topK must be an integer", 400)

    result = await synatyx.run_tool("context_retrieve", {
        "query": values["query"],
        "user_id": values["userId"],
        "project": values["project"],
        "top_k": top_k,
        "memory_layers": ["L1", "L2"],
    })
    failure = _tool_failed(result)
    if failure is not None:
        return failure

    return JSONResponse({
        "items": [
            {
                "id": item.get("id"),
                "content": item.get("content", ""),
                "score": item.get("score"),
                "metadata": item.get("metadata") or {},
            }
            for item in result.get("context_items", [])
        ]
    })


async def memory_summarize(request: Request) -> JSONResponse:
    """Summarize a conversation's working memory, and return the summary."""
    synatyx, body, err = await _parse(request)
    if err is not None:
        return err
    assert body is not None

    values, err = _require(body, {
        "project": ("project",),
        "userId": ("userId", "user_id"),
        "conversationId": ("conversationId", "session_id", "sessionId"),
    })
    if err is not None:
        return err

    try:
        result = await synatyx.summarize_session(
            user_id=values["userId"],
            project=values["project"],
            session_id=values["conversationId"],
            max_tokens=int(_field(body, "maxTokens", "max_tokens", default=500)),
            focus=_text(body, "focus") or None,
        )
    except (TypeError, ValueError) as exc:
        return error(INVALID_REQUEST, str(exc), 400)
    except Exception:
        logger.exception("Summarize failed for conversation %r", values["conversationId"])
        return error(UPSTREAM_UNAVAILABLE, "Summarize failed", 502)

    # An empty summary means there was nothing in the window to summarize —
    # a normal answer for a conversation that has just started.
    return JSONResponse({
        "summary": result["summary"],
        "keyEntities": result["key_entities"],
        "tokensSaved": result["tokens_saved"],
    })


# ---------------------------------------------------------------------------
# Erasure
# ---------------------------------------------------------------------------

async def erase_user(request: Request) -> JSONResponse:
    """Delete one user's stored items everywhere. Not reversible."""
    synatyx = getattr(request.app.state, "synatyx", None)
    if synatyx is None:
        return error(NOT_READY, "Server is still starting", 503)

    user_id = (request.path_params.get("user_id") or "").strip()
    if not user_id:
        return error(INVALID_REQUEST, "user_id path parameter is required", 400)

    try:
        result = await synatyx.erase_user(user_id)
    except Exception:
        logger.exception("Erase failed for user %r", user_id)
        return error(UPSTREAM_UNAVAILABLE, "Erase failed", 502)

    failed = result.get("collections_failed") or []
    if failed:
        # A partial erasure must not report success: the caller is answering a
        # data-subject request and needs to know it is not finished.
        return error(
            INTERNAL_ERROR,
            "Erasure incomplete — some collections could not be purged",
            500,
            {"collectionsPurged": result.get("collections_purged"), "collectionsFailed": failed},
        )

    return JSONResponse({
        "userId": user_id,
        "collectionsPurged": result.get("collections_purged", []),
    })


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

async def health(request: Request) -> JSONResponse:
    """Readiness for callers that depend on Synatyx.

    Deliberately not a bare 200: a process that is up while its vector store
    is unreachable is exactly the state a dependent service's readiness probe
    exists to catch. The backend check is cached for a few seconds so probe
    traffic costs one round trip per interval however often it is called.
    """
    global _health_cache

    now = time.monotonic()
    if _health_cache is not None and now - _health_cache[0] < _HEALTH_CACHE_SECONDS:
        payload = _health_cache[1]
        return JSONResponse(payload, status_code=200 if payload["status"] == "ok" else 503)

    qdrant = getattr(request.app.state, "qdrant", None)
    if qdrant is None:
        payload = {"status": "starting", "vectorStore": "unknown"}
    else:
        try:
            alive = await qdrant.ping()
            payload = {
                "status": "ok" if alive else "degraded",
                "vectorStore": "ok" if alive else "unreachable",
            }
        except Exception:
            logger.exception("Health check failed")
            payload = {"status": "degraded", "vectorStore": "unreachable"}

    _health_cache = (now, payload)
    return JSONResponse(payload, status_code=200 if payload["status"] == "ok" else 503)


routes = [
    Route("/v1/health", _enveloped(health)),
    Route("/v1/ingest", _enveloped(ingest), methods=["POST"]),
    Route("/v1/retrieve", _enveloped(retrieve), methods=["POST"]),
    Route("/v1/sources/deprecate", _enveloped(deprecate_source), methods=["POST"]),
    Route("/v1/memory/store", _enveloped(memory_store), methods=["POST"]),
    Route("/v1/memory/retrieve", _enveloped(memory_retrieve), methods=["POST"]),
    Route("/v1/memory/summarize", _enveloped(memory_summarize), methods=["POST"]),
    Route("/v1/users/{user_id}", _enveloped(erase_user), methods=["DELETE"]),
]
