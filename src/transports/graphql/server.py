from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import strawberry
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from mcp.server.sse import SseServerTransport
from starlette.routing import Mount, Route
from strawberry.fastapi import GraphQLRouter

from src.config import settings
from src.core.budget import BudgetManager
from src.core.retrieve import RetrieveService
from src.core.store import StoreService
from src.core.summarize import SummarizeService
from src.storage.postgres import PostgresStorage
from src.storage.qdrant import QdrantStorage
from src.storage.redis import RedisStorage
from src.transports.graphql.schema.mutations import Mutation
from src.transports.graphql.schema.queries import Query
from src.transports.graphql.schema.subscriptions import Subscription
from src.transports.mcp.server import SynatyxMCPServer

_mcp_server: SynatyxMCPServer | None = None


# ---------------------------------------------------------------------------
# Dependency containers (shared across requests)
# ---------------------------------------------------------------------------

_qdrant: QdrantStorage | None = None
_redis: RedisStorage | None = None
_postgres: PostgresStorage | None = None
_retrieve_svc: RetrieveService | None = None
_store_svc: StoreService | None = None
_summarize_svc: SummarizeService | None = None
_mcp_sse: SseServerTransport | None = None


async def get_context() -> dict[str, Any]:
    return {
        "qdrant": _qdrant,
        "redis": _redis,
        "postgres": _postgres,
        "retrieve_service": _retrieve_svc,
        "store_service": _store_svc,
        "summarize_service": _summarize_svc,
        "budget_manager": BudgetManager(),
    }


# ---------------------------------------------------------------------------
# Strawberry schema
# ---------------------------------------------------------------------------

schema = strawberry.Schema(
    query=Query,
    mutation=Mutation,
    subscription=Subscription,
)


# ---------------------------------------------------------------------------
# FastAPI app with lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _qdrant, _redis, _postgres, _retrieve_svc, _store_svc, _summarize_svc, _mcp_server, _mcp_sse

    _qdrant = QdrantStorage(host=settings.qdrant.host, port=settings.qdrant.port, collection_name=settings.qdrant.collection_name)
    await _qdrant.init_collection()

    _redis = RedisStorage(url=settings.redis.url)
    await _redis.ping()

    _postgres = PostgresStorage(dsn=settings.postgres.dsn)
    await _postgres.connect()

    _retrieve_svc = RetrieveService(_qdrant, _redis, _postgres)
    _store_svc = StoreService(_qdrant, _redis, _postgres)
    _summarize_svc = SummarizeService(_redis, _postgres)

    _mcp_server = SynatyxMCPServer(_qdrant, _redis, _postgres)
    _mcp_sse = SseServerTransport("/mcp/messages/")

    yield

    await _qdrant.close()
    await _redis.close()
    await _postgres.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Synatyx Context Engine",
        description="Open-source LLM memory layer — GraphQL API",
        version="0.1.0",
        lifespan=lifespan,
    )

    graphql_router = GraphQLRouter(
        schema,
        context_getter=get_context,
        graphql_ide="graphiql" if settings.debug else None,
        subscription_protocols=["graphql-transport-ws"],
    )

    app.include_router(graphql_router, prefix="/graphql")

    # ── MCP HTTP/SSE endpoint ──────────────────────────────────────────────
    @app.get("/mcp/sse")
    async def mcp_sse_connect(request: Request) -> None:
        """
        MCP SSE connection endpoint.
        Clients connect here to establish the MCP session.

        OpenClaw / Claude Desktop config:
            { "url": "http://localhost:8000/mcp/sse" }
        """
        async with _mcp_sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await _mcp_server._server.run(
                streams[0],
                streams[1],
                _mcp_server._server.create_initialization_options(),
            )

    async def _mcp_messages_app(scope, receive, send):
        """Defer _mcp_sse lookup to request time (set during lifespan)."""
        await _mcp_sse.handle_post_message(scope, receive, send)

    app.mount("/mcp/messages/", app=_mcp_messages_app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "synatyx-context-engine"}

    @app.get("/v1/stream/events")
    async def sse_events(request: Request, user_id: str) -> StreamingResponse:
        """
        Server-Sent Events endpoint for real-time context engine events.
        Ported from CTX-EG SSE protocol (docs/SSE_PROTOCOL.md).

        Connect: GET /v1/stream/events?user_id=<user_id>
        Events:  context_stored | session_summarized | budget_alert | ping

        Client example:
            const es = new EventSource('/v1/stream/events?user_id=user-123');
            es.addEventListener('context_stored', e => console.log(JSON.parse(e.data)));
        """
        redis = _redis

        async def event_generator() -> AsyncGenerator[str, None]:
            pubsub = await redis.subscribe()
            try:
                # Initial ping to confirm connection
                yield f"event: ping\ndata: {json.dumps({'user_id': user_id})}\n\n"

                while True:
                    if await request.is_disconnected():
                        break

                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0
                    )

                    if message and message["type"] == "message":
                        try:
                            data = json.loads(message["data"])
                            event_type = data.get("event", "unknown")
                            payload = data.get("payload", {})

                            # Only emit events for this user
                            if payload.get("user_id") == user_id:
                                yield f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"
                        except (json.JSONDecodeError, KeyError):
                            continue
                    else:
                        # Keepalive ping every ~30s of silence
                        yield ": keepalive\n\n"
                        await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                pass
            finally:
                await pubsub.unsubscribe("synatyx:events")
                await pubsub.close()

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    return app


app = create_app()

