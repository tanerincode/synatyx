from __future__ import annotations

import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from src.core.budget import BudgetManager
from src.core.ingest import IngestService
from src.core.project import ProjectManager
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
        self._default_qdrant = qdrant
        self._redis = redis
        self._postgres = postgres
        self._project_manager = ProjectManager(redis, qdrant)
        # Service cache keyed by collection_name — avoids re-creating services on every call
        self._svc_cache: dict[str, tuple[RetrieveService, StoreService, IngestService]] = {}
        self._budget = BudgetManager()
        self._register_handlers()

    async def _get_l4_services(self) -> tuple[QdrantStorage, StoreService, RetrieveService]:
        """Return services backed by the shared ctx_users collection (L4 only)."""
        storage = await self._project_manager.get_l4_storage()
        key = storage.collection_name
        if key not in self._svc_cache:
            store_svc = StoreService(storage, self._redis, self._postgres)
            retrieve_svc = RetrieveService(storage, self._redis, self._postgres)
            ingest_svc = IngestService(store_svc)
            self._svc_cache[key] = (retrieve_svc, store_svc, ingest_svc)
        retrieve, store, _ = self._svc_cache[key]
        return storage, store, retrieve

    async def _get_services(
        self, user_id: str
    ) -> tuple[QdrantStorage, RetrieveService, StoreService, IngestService, str | None]:
        """Return project-scoped services for the given user.

        Returns:
            (storage, retrieve, store, ingest, cwd_suggestion)
            cwd_suggestion is non-None only when no project has been set yet.
        """
        storage, suggestion = await self._project_manager.get_storage(user_id)
        key = storage.collection_name
        if key not in self._svc_cache:
            store_svc = StoreService(storage, self._redis, self._postgres)
            retrieve_svc = RetrieveService(storage, self._redis, self._postgres)
            ingest_svc = IngestService(store_svc)
            self._svc_cache[key] = (retrieve_svc, store_svc, ingest_svc)
        retrieve, store, ingest = self._svc_cache[key]
        return storage, retrieve, store, ingest, suggestion

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
        user_id = args.get("user_id", "")

        # ── Project management (no storage needed) ──────────────────────────
        if name == "context_set_project":
            slug, storage = await self._project_manager.set_project(user_id, args["project"])
            return {
                "project": slug,
                "collection": storage.collection_name,
                "message": f"Active project set to '{slug}' (collection: '{storage.collection_name}').",
            }

        elif name == "context_get_project":
            slug = await self._project_manager.get_project(user_id)
            if slug:
                return {"project": slug, "collection": f"ctx_{slug}", "suggestion": None}
            from src.core.project import _detect_cwd_name
            suggestion = _detect_cwd_name()
            return {
                "project": None,
                "collection": None,
                "suggestion": suggestion,
                "message": (
                    f"No project set. Detected workspace folder '{suggestion}'. "
                    f"Call context_set_project with project='{suggestion}' to confirm."
                ),
            }

        # ── All other tools — route to the active project's storage ─────────
        storage, retrieve, store, ingest, suggestion = await self._get_services(user_id)
        _warn: dict[str, Any] = (
            {"_project_warning": f"No project set. Detected workspace '{suggestion}'. Call context_set_project to confirm."}
            if suggestion else {}
        )

        if name == "context_retrieve":
            requested = [MemoryLayer(l) for l in args.get("memory_layers", [])] or list(MemoryLayer)
            top_k = args.get("top_k", 10)

            # Split: L4 always comes from ctx_users; everything else from the project collection
            project_layers = [l for l in requested if l != MemoryLayer.L4]
            include_l4 = MemoryLayer.L4 in requested

            combined_items = []
            suggested_budget: dict = {}

            if project_layers:
                proj_result = await retrieve.retrieve(
                    query=args["query"],
                    user_id=user_id,
                    session_id=args.get("session_id"),
                    project=args.get("project"),
                    top_k=top_k,
                    memory_layers=project_layers,
                )
                combined_items.extend(proj_result.context_items)
                suggested_budget = proj_result.suggested_budget

            if include_l4:
                _, _, l4_retrieve = await self._get_l4_services()
                l4_result = await l4_retrieve.retrieve(
                    query=args["query"],
                    user_id=user_id,
                    session_id=args.get("session_id"),
                    top_k=top_k,
                    memory_layers=[MemoryLayer.L4],
                )
                combined_items.extend(l4_result.context_items)
                suggested_budget = suggested_budget or l4_result.suggested_budget

            combined_items.sort(key=lambda x: x.score, reverse=True)
            final_items = combined_items[:top_k]
            total_tokens = sum(i.token_estimate for i in final_items)

            return {
                "context_items": [i.model_dump() for i in final_items],
                "total_tokens": total_tokens,
                "suggested_budget": suggested_budget,
                **_warn,
            }

        elif name == "context_store":
            layer = MemoryLayer(args["memory_layer"])
            # L4 is user-global — always goes to ctx_users, not the active project collection
            _store = store if layer != MemoryLayer.L4 else (await self._get_l4_services())[1]
            item_id, embedded = await _store.store(
                content=args["content"],
                user_id=user_id,
                memory_layer=layer,
                importance=args.get("importance", 0.5),
                session_id=args.get("session_id"),
                metadata=args.get("metadata"),
                confidence=args.get("confidence", 1.0),
            )
            return {"item_id": item_id, "embedded": embedded, **_warn}

        elif name == "context_summarize":
            summarize = SummarizeService(self._redis, self._postgres, store=store)
            await summarize.summarize_async(
                session_id=args["session_id"],
                user_id=user_id,
                max_tokens=args.get("max_tokens", 500),
                focus=args.get("focus"),
            )
            return {"status": "summarization_scheduled", **_warn}

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
            result = await ingest.ingest(
                source=args["source"],
                user_id=user_id,
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
                **_warn,
            }

        elif name == "context_checkpoint":
            item_id, embedded = await store.checkpoint(
                name=args["name"],
                content=args["content"],
                user_id=user_id,
                project=args.get("project"),
                session_id=args.get("session_id"),
            )
            return {"item_id": item_id, "embedded": embedded, "checkpoint_name": args["name"], **_warn}

        elif name == "context_deprecate":
            await store.deprecate(
                item_id=args["item_id"],
                user_id=user_id,
                reason=args.get("reason"),
            )
            return {"deprecated": True, "item_id": args["item_id"]}

        elif name == "context_list":
            from src.models.memory_layer import MemoryLayer as ML
            layer_str = args.get("memory_layer")
            layer = ML(layer_str) if layer_str else None
            # L4 lives in ctx_users — route list calls there when the filter is explicitly L4
            _list_storage = (await self._get_l4_services())[0] if layer == ML.L4 else storage
            items = await _list_storage.list_items(
                user_id=user_id,
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
                **_warn,
            }

        elif name == "context_task_add":
            from src.models.task import Task, TaskPriority, TaskStatus
            task = Task(
                user_id=user_id,
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
                user_id=user_id,
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
                user_id=user_id,
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

