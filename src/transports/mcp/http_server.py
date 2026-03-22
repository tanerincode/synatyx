from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from src.config import settings
from src.storage.postgres import PostgresStorage
from src.storage.qdrant import QdrantStorage
from src.storage.redis import RedisStorage
from src.transports.mcp.server import SynatyxMCPServer

logger = logging.getLogger(__name__)

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
    # picks it up on every incoming request.
    mcp._mcp_server = synatyx._server

    logger.info("Synatyx MCP HTTP server ready on %s:%d", _host, _port)

    yield

    await qdrant.close()
    await redis.close()
    await postgres.close()


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "synatyx-mcp"})


# ---------------------------------------------------------------------------
# ASGI app — FastMCP SSE routes + /health, wrapped with lifespan.
# ---------------------------------------------------------------------------

_sse_app = mcp.sse_app()

app = Starlette(
    routes=_sse_app.routes + [Route("/health", health)],
    lifespan=lifespan,
)

