from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import strawberry
from fastapi import FastAPI
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


# ---------------------------------------------------------------------------
# Dependency containers (shared across requests)
# ---------------------------------------------------------------------------

_qdrant: QdrantStorage | None = None
_redis: RedisStorage | None = None
_postgres: PostgresStorage | None = None
_retrieve_svc: RetrieveService | None = None
_store_svc: StoreService | None = None
_summarize_svc: SummarizeService | None = None


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
    global _qdrant, _redis, _postgres, _retrieve_svc, _store_svc, _summarize_svc

    _qdrant = QdrantStorage(
        host=settings.qdrant.host,
        port=settings.qdrant.port,
    )
    await _qdrant.init_collection()

    _redis = RedisStorage(url=settings.redis.url)
    await _redis.ping()

    _postgres = PostgresStorage(dsn=settings.postgres.dsn)
    await _postgres.connect()

    _retrieve_svc = RetrieveService(_qdrant, _redis, _postgres)
    _store_svc = StoreService(_qdrant, _redis, _postgres)
    _summarize_svc = SummarizeService(_redis, _postgres)

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
        graphiql=settings.debug,
        subscription_protocols=["graphql-transport-ws"],
    )

    app.include_router(graphql_router, prefix="/graphql")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "synatyx-context-engine"}

    return app


app = create_app()

