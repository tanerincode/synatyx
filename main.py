from __future__ import annotations

import logging
import os

import uvicorn

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger("synatyx")


def main() -> None:
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    debug = os.getenv("DEBUG", "false").lower() == "true"

    logger.info("Starting Synatyx Context Engine on %s:%d", host, port)
    logger.info("GraphQL:       http://%s:%d/graphql", host, port)
    logger.info("GraphiQL:      http://%s:%d/graphql (debug=%s)", host, port, debug)
    logger.info("SSE Events:    http://%s:%d/v1/stream/events?user_id=<id>", host, port)
    logger.info("MCP (SSE):     http://%s:%d/mcp/sse", host, port)
    logger.info("Health:        http://%s:%d/health", host, port)

    uvicorn.run(
        "src.transports.graphql.server:app",
        host=host,
        port=port,
        reload=debug,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()

