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
# FastMCP instance
# SynatyxMCPServer (with all tool handlers) is injected at startup via
# lifespan so FastMCP's SSE transport carries our full tool set.
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
# Lifespan — init storage once at app startup, inject into FastMCP
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

    # Build the full MCP server (registers all ~20 tools on the low-level Server)
    # then inject it into FastMCP — handle_sse reads mcp._mcp_server at
    # request time so the injection is safe before any request arrives.
    synatyx = SynatyxMCPServer(qdrant, redis, postgres)
    mcp._mcp_server = synatyx._server

    logger.info("Synatyx tools injected into FastMCP — ready")

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
# ASGI app — FastMCP SSE routes + health, wrapped with our lifespan
# ---------------------------------------------------------------------------

_sse_app = mcp.sse_app()

app = Starlette(
    routes=_sse_app.routes + [Route("/health", health)],
    lifespan=lifespan,
)

