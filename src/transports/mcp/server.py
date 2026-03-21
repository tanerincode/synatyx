from __future__ import annotations

import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from src.core.budget import BudgetManager
from src.core.retrieve import RetrieveService
from src.core.score import score_items
from src.core.store import StoreService
from src.core.summarize import SummarizeService
from src.models.memory_layer import MemoryLayer
from src.storage.postgres import PostgresStorage
from src.storage.qdrant import QdrantStorage
from src.storage.redis import RedisStorage
from src.transports.mcp.tools import TOOL_DEFINITIONS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

class SynatyxMCPServer:
    def __init__(
        self,
        qdrant: QdrantStorage,
        redis: RedisStorage,
        postgres: PostgresStorage,
    ) -> None:
        self._server = Server("synatyx-context-engine")
        self._retrieve = RetrieveService(qdrant, redis, postgres)
        self._store = StoreService(qdrant, redis, postgres)
        self._summarize = SummarizeService(redis, postgres, store=self._store)
        self._budget = BudgetManager()
        self._register_handlers()

    def _register_handlers(self) -> None:
        @self._server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name=t["name"],
                    description=t["description"],
                    inputSchema=t["parameters"],
                )
                for t in TOOL_DEFINITIONS
            ]

        @self._server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
            import json
            try:
                result = await self._dispatch(name, arguments)
            except Exception as exc:
                logger.exception("Tool %r raised an error", name)
                result = {"error": str(exc), "tool": name}
            return [TextContent(type="text", text=json.dumps(result, default=str))]

    async def _dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "context_retrieve":
            layers = [MemoryLayer(l) for l in args.get("memory_layers", [])] or None
            result = await self._retrieve.retrieve(
                query=args["query"],
                user_id=args["user_id"],
                session_id=args.get("session_id"),
                top_k=args.get("top_k", 10),
                memory_layers=layers,
            )
            return {
                "context_items": [i.model_dump() for i in result.context_items],
                "total_tokens": result.total_tokens,
                "suggested_budget": result.suggested_budget,
            }

        elif name == "context_store":
            item_id, embedded = await self._store.store(
                content=args["content"],
                user_id=args["user_id"],
                memory_layer=MemoryLayer(args["memory_layer"]),
                importance=args.get("importance", 0.5),
                session_id=args.get("session_id"),
                metadata=args.get("metadata"),
                confidence=args.get("confidence", 1.0),
            )
            return {"item_id": item_id, "embedded": embedded}

        elif name == "context_summarize":
            await self._summarize.summarize_async(
                session_id=args["session_id"],
                user_id=args["user_id"],
                max_tokens=args.get("max_tokens", 500),
                focus=args.get("focus"),
            )
            return {"status": "summarization_scheduled"}

        elif name == "context_score":
            from src.models.context import ContextItem
            items = [ContextItem(**i) for i in args["items"]]
            scored, dropped = score_items(items, args["query"])
            return {
                "scored_items": [i.model_dump() for i in scored],
                "dropped_items": [i.model_dump() for i in dropped],
            }

        raise ValueError(f"Unknown tool: {name}")

    async def run_stdio(self) -> None:
        """Run the MCP server over stdio (for OpenClaw / Claude Desktop)."""
        async with stdio_server() as (read_stream, write_stream):
            await self._server.run(read_stream, write_stream, self._server.create_initialization_options())

