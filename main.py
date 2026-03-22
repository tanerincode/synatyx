from __future__ import annotations

import asyncio
import logging
import os

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("synatyx")


def _run_mcp_http(host: str, port: int, debug: bool) -> None:
    import uvicorn
    logger.info("MCP SSE  : http://%s:%d/mcp/sse", host, port)
    logger.info("Health   : http://%s:%d/health", host, port)
    uvicorn.run(
        "src.transports.mcp.http_server:app",
        host=host,
        port=port,
        reload=debug,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


async def _connect_with_retry(label: str, connect_fn, *, max_attempts: int = 5, base_delay: float = 1.0):
    """Try connect_fn up to max_attempts times with exponential back-off. Returns True on success."""
    for attempt in range(1, max_attempts + 1):
        try:
            await connect_fn()
            logger.info("%s connected", label)
            return True
        except Exception as exc:
            delay = base_delay * (2 ** (attempt - 1))
            if attempt < max_attempts:
                logger.warning("%s unavailable (attempt %d/%d): %s — retrying in %.0fs",
                               label, attempt, max_attempts, exc, delay)
                await asyncio.sleep(delay)
            else:
                logger.error("%s still unavailable after %d attempts — continuing in degraded mode: %s",
                             label, max_attempts, exc)
    return False


async def _run_mcp_stdio() -> None:
    from src.config import settings
    from src.storage.qdrant import QdrantStorage
    from src.storage.redis import RedisStorage
    from src.storage.postgres import PostgresStorage
    from src.transports.mcp.server import SynatyxMCPServer

    qdrant = QdrantStorage(host=settings.qdrant.host, port=settings.qdrant.port, collection_name=settings.qdrant.collection_name)
    await _connect_with_retry("Qdrant", qdrant.init_collection)

    redis = RedisStorage(url=settings.redis.url)
    await _connect_with_retry("Redis", redis.ping)

    postgres = PostgresStorage(dsn=settings.postgres.dsn)
    await _connect_with_retry("PostgreSQL", postgres.connect)

    server = SynatyxMCPServer(qdrant, redis, postgres)
    logger.info("MCP stdio server starting...")
    await server.run_stdio()


def main() -> None:
    from src.config import settings, RunMode

    mode = settings.run_mode

    logger.info("Synatyx Context Engine — mode: %s", mode.value)

    if mode == RunMode.MCP:
        logger.info("Running MCP stdio server only")
        asyncio.run(_run_mcp_stdio())

    elif mode == RunMode.MCP_HTTP:
        logger.info("Running MCP HTTP/SSE server only")
        host = os.getenv("HOST", "0.0.0.0")
        port = int(os.getenv("PORT", "9000"))
        _run_mcp_http(host, port, settings.debug)

    elif mode == RunMode.GC:
        logger.info("Running GC daemon")
        asyncio.run(_run_gc())


async def _run_gc() -> None:
    from src.core.gc import GarbageCollector
    from src.config import settings
    from src.storage.postgres import PostgresStorage
    from src.storage.qdrant import QdrantStorage

    interval_seconds = settings.gc.run_interval_hours * 3600
    logger.info(
        "GC daemon started — interval=%dh  L2_ttl=%dd  L3_ttl=%dd",
        settings.gc.run_interval_hours,
        settings.gc.l2_base_ttl_days,
        settings.gc.l3_base_ttl_days,
    )

    qdrant = QdrantStorage(host=settings.qdrant.host, port=settings.qdrant.port, collection_name="ctx_system")
    postgres = PostgresStorage(dsn=settings.postgres.dsn)
    await _connect_with_retry("Qdrant", qdrant.init_collection)
    await _connect_with_retry("PostgreSQL", postgres.connect)

    gc = GarbageCollector(qdrant=qdrant, postgres=postgres, settings=settings.gc)

    while True:
        if settings.gc.enabled:
            try:
                summary = await gc.run_once()
                logger.info("GC pass complete: %s", summary)
            except Exception as exc:
                logger.error("GC pass failed: %s", exc, exc_info=True)
        else:
            logger.info("GC disabled — sleeping")
        await asyncio.sleep(interval_seconds)


if __name__ == "__main__":
    main()

