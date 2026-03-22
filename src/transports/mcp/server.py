from __future__ import annotations

import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from src.core.budget import BudgetManager
from src.core.ingest import IngestService
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
        self._qdrant = qdrant
        self._retrieve = RetrieveService(qdrant, redis, postgres)
        self._store = StoreService(qdrant, redis, postgres)
        self._summarize = SummarizeService(redis, postgres, store=self._store)
        self._ingest = IngestService(self._store)
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
                project=args.get("project"),
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

        elif name == "context_ingest":
            from src.models.memory_layer import MemoryLayer as ML
            layer_str = args.get("memory_layer", "L3")
            result = await self._ingest.ingest(
                source=args["source"],
                user_id=args["user_id"],
                memory_layer=ML(layer_str),
                importance=float(args.get("importance", 0.8)),
                project=args.get("project"),
                session_id=args.get("session_id"),
            )
            return {
                "source": result.source,
                "chunks_stored": result.chunks_stored,
                "chunks_failed": result.chunks_failed,
                "total_chunks": result.total_chunks,
            }

        elif name == "context_checkpoint":
            item_id, embedded = await self._store.checkpoint(
                name=args["name"],
                content=args["content"],
                user_id=args["user_id"],
                project=args.get("project"),
                session_id=args.get("session_id"),
            )
            return {"item_id": item_id, "embedded": embedded, "checkpoint_name": args["name"]}

        elif name == "context_deprecate":
            await self._store.deprecate(
                item_id=args["item_id"],
                user_id=args["user_id"],
                reason=args.get("reason"),
            )
            return {"deprecated": True, "item_id": args["item_id"]}

        elif name == "context_list":
            from src.models.memory_layer import MemoryLayer as ML
            layer_str = args.get("memory_layer")
            layer = ML(layer_str) if layer_str else None
            items = await self._qdrant.list_items(
                user_id=args["user_id"],
                memory_layer=layer,
                checkpoints_only=args.get("checkpoints_only", False),
                include_deprecated=args.get("include_deprecated", False),
                project=args.get("project"),
                limit=args.get("limit", 50),
            )
            return {
                "items": [
                    {
                        "id": i.id,
                        "memory_layer": i.memory_layer.value,
                        "content": i.content[:200],
                        "importance": i.importance,
                        "is_pinned": i.is_pinned,
                        "is_deprecated": i.is_deprecated,
                        "metadata": i.metadata,
                    }
                    for i in items
                ],
                "count": len(items),
            }

        elif name == "context_task_add":
            from src.models.task import Task, TaskPriority, TaskStatus
            task = Task(
                user_id=args["user_id"],
                title=args["title"],
                description=args.get("description", ""),
                priority=TaskPriority(args.get("priority", "medium")),
                project=args.get("project"),
            )
            saved = await self._postgres.task_add(task)
            return {"task_id": saved.id, "title": saved.title, "status": saved.status, "priority": saved.priority}

        elif name == "context_task_list":
            from src.models.task import TaskPriority, TaskStatus
            status_str = args.get("status", "pending")
            priority_str = args.get("priority")
            tasks = await self._postgres.task_list(
                user_id=args["user_id"],
                status=TaskStatus(status_str) if status_str else None,
                priority=TaskPriority(priority_str) if priority_str else None,
                project=args.get("project"),
                limit=args.get("limit", 50),
            )
            return {
                "tasks": [
                    {
                        "id": t.id,
                        "title": t.title,
                        "description": t.description,
                        "status": t.status,
                        "priority": t.priority,
                        "project": t.project,
                        "created_at": t.created_at.isoformat(),
                    }
                    for t in tasks
                ],
                "count": len(tasks),
            }

        elif name == "context_task_update":
            from src.models.task import TaskPriority, TaskStatus
            status_str = args.get("status")
            priority_str = args.get("priority")
            updated = await self._postgres.task_update(
                task_id=args["task_id"],
                user_id=args["user_id"],
                status=TaskStatus(status_str) if status_str else None,
                priority=TaskPriority(priority_str) if priority_str else None,
                title=args.get("title"),
                description=args.get("description"),
            )
            if not updated:
                return {"error": f"Task {args['task_id']!r} not found"}
            return {"task_id": updated.id, "title": updated.title, "status": updated.status, "updated_at": updated.updated_at.isoformat()}

        raise ValueError(f"Unknown tool: {name}")

    async def run_stdio(self) -> None:
        """Run the MCP server over stdio (for OpenClaw / Claude Desktop)."""
        async with stdio_server() as (read_stream, write_stream):
            await self._server.run(read_stream, write_stream, self._server.create_initialization_options())

