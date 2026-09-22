from __future__ import annotations

import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from src.config import settings
from src.core.alternatives import AlternativesService
from src.core.brief import BriefService
from src.core.budget import BudgetManager, estimate_tokens
from src.core.ingest import IngestService
from src.core.project import ProjectManager
from src.core.relation import RelationService
from src.core.retrieve import RetrieveService, empty_retrieve_diagnostics
from src.core.score import score_items
from src.core.skill import SkillService
from src.core.store import StoreService
from src.core.summarize import SummarizeService
from src.core.tracking import SessionTracker
from src.core.usage import UsageRecorder, usage_begin, usage_end
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
        self._skill_svc_cache: dict[str, SkillService] = {}
        self._pack_svc_cache: dict[str, Any] = {}
        self._index_svc_cache: dict[str, Any] = {}
        self._tracker = SessionTracker(redis, settings.tracking)
        self._usage = UsageRecorder(postgres, settings.usage)
        self._register_handlers()

    async def _get_skill_service(self, user_id: str, project: str | None = None) -> SkillService:
        """Return a SkillService backed by the active project's Qdrant collection."""
        storage, _, _, _, _ = await self._get_services(user_id, project)
        key = storage.collection_name
        if key not in self._skill_svc_cache:
            self._skill_svc_cache[key] = SkillService(storage, self._postgres)
        return self._skill_svc_cache[key]

    async def _get_pack_service(self, user_id: str, project: str | None = None):
        """Return a PackService backed by the active project's collection.

        Invalidated by context_index so a freshly built code index is picked
        up on the next pack call."""
        from src.core.pack import PackService

        storage, retrieve, _, _, _ = await self._get_services(user_id, project)
        key = storage.collection_name
        if key not in self._pack_svc_cache:
            l4_storage = await self._project_manager.get_l4_storage()
            relations = RelationService(self._postgres, storage, l4_storage)
            skills = SkillService(storage, self._postgres)
            index_search = await self._maybe_index_search(storage)
            self._pack_svc_cache[key] = PackService(
                retrieve, storage, l4_storage, self._postgres,
                relations, skills, index_search,
            )
        return self._pack_svc_cache[key]

    async def _maybe_index_search(self, storage: QdrantStorage):
        """Return an IndexSearchService when the project has an index
        collection, else None."""
        from src.core.index import IndexSearchService
        from src.core.project import index_collection_for

        slug = storage.project_slug
        if not slug:
            return None
        index_collection = index_collection_for(slug)
        try:
            collections = await storage.get_all_collections()
        except Exception:
            return None
        if index_collection not in collections:
            return None
        return IndexSearchService(storage.scoped(index_collection))

    async def _get_index_services(self, user_id: str, project: str | None = None):
        """Return (IndexService, IndexSearchService) for the project's code/doc
        index collection. Requires a resolvable project — indexing into a
        default collection would orphan chunks on project rename."""
        from src.core.index import IndexSearchService, IndexService

        if project:
            slug = project
        else:
            slug = await self._project_manager.get_project(user_id)
        if not slug:
            raise ValueError(
                "context_index requires a project — call context_set_project "
                "first or pass the project argument."
            )
        if slug not in self._index_svc_cache:
            storage = await self._project_manager.get_index_storage(slug)
            self._index_svc_cache[slug] = (
                IndexService(storage, slug),
                IndexSearchService(storage),
            )
        return self._index_svc_cache[slug]

    async def _get_relation_service(
        self, user_id: str, project: str | None = None
    ) -> RelationService:
        """Return a RelationService spanning the active project collection and ctx_users."""
        storage, _, _, _, _ = await self._get_services(user_id, project)
        l4_storage = await self._project_manager.get_l4_storage()
        return RelationService(self._postgres, storage, l4_storage)

    async def _get_alternatives_service(
        self, user_id: str, project: str | None = None
    ) -> AlternativesService:
        storage, _, _, _, _ = await self._get_services(user_id, project)
        l4_storage = await self._project_manager.get_l4_storage()
        relations = RelationService(self._postgres, storage, l4_storage)
        return AlternativesService(storage, l4_storage, self._postgres, relations)

    async def _detect_alternatives_safe(
        self, user_id: str, item_id: str, project: str | None = None
    ) -> dict[str, Any]:
        """Run same-purpose detection after a store; never let it break the store."""
        try:
            alternatives = await self._get_alternatives_service(user_id, project)
            return await alternatives.detect_for_item(user_id, item_id)
        except Exception:
            logger.exception("Alternative detection failed for item %s", item_id)
            return {"auto_linked": [], "suggestions": []}

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
        self, user_id: str, project: str | None = None
    ) -> tuple[QdrantStorage, RetrieveService, StoreService, IngestService, str | None]:
        """Return project-scoped services for the given user.

        When `project` is given, it wins over the user's active-project pointer
        (which is a single per-user value shared by ALL concurrent sessions —
        routing by the explicit argument keeps parallel sessions from storing
        into each other's collections).

        Returns:
            (storage, retrieve, store, ingest, cwd_suggestion)
            cwd_suggestion is non-None only when no project has been set yet.
        """
        if project:
            storage = await self._project_manager.get_storage_for(project)
            suggestion = None
        else:
            storage, suggestion = await self._project_manager.get_storage(user_id)
        key = storage.collection_name
        if key not in self._svc_cache:
            store_svc = StoreService(storage, self._redis, self._postgres)
            retrieve_svc = RetrieveService(storage, self._redis, self._postgres)
            ingest_svc = IngestService(store_svc)
            self._svc_cache[key] = (retrieve_svc, store_svc, ingest_svc)
        retrieve, store, ingest = self._svc_cache[key]
        return storage, retrieve, store, ingest, suggestion

    async def capture(
        self,
        user_id: str,
        content: str,
        session_id: str | None = None,
        project: str | None = None,
        memory_layer: str = "L2",
        importance: float = 0.6,
        metadata: dict[str, Any] | None = None,
        origin: str | None = None,
    ) -> dict[str, Any]:
        """Store a memory pushed from outside the MCP loop (session-end hooks,
        CI jobs, cron). Same pipeline as context_store — sanitization,
        chunking, provenance, alternative detection are all preserved."""
        from src.models.memory_layer import MemoryLayer as ML

        _, _, store, _, _ = await self._get_services(user_id, project)
        layer = ML(memory_layer)
        if layer == ML.L4:
            store = (await self._get_l4_services())[1]

        meta = {"source": "capture", **(metadata or {})}
        if project:
            meta.setdefault("project", project)

        item_ids, embedded = await store.store(
            content=content,
            user_id=user_id,
            memory_layer=layer,
            importance=importance,
            session_id=session_id,
            metadata=meta,
            origin=origin,
        )
        return {"item_id": item_ids[0], "item_ids": item_ids, "embedded": embedded}

    async def run_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run one tool exactly as the MCP transport runs it, returning the raw dict.

        Everything that wraps a tool call — token metering, implicit session
        capture, turning an exception into an `error` result instead of a
        stack trace — belongs to the call, not to the transport that carried
        it. Keeping it here is what lets a second transport (the REST API)
        expose the same tools without either duplicating that wrapper or
        quietly skipping half of it, which is how a REST caller's spend would
        otherwise go unmetered.
        """
        import json

        usage_begin()
        failed = False
        try:
            result = await self._dispatch(name, arguments)
        except Exception as exc:
            logger.exception("Tool %r raised an error", name)
            # The exception's type rides along so a caller can tell a bad
            # argument from a backend that fell over. Both arrive here as an
            # `error` string, and a transport that cannot distinguish them
            # reports the caller's own typo as an outage.
            result = {"error": str(exc), "tool": name, "error_type": type(exc).__name__}
            failed = True

        # record() swallows its own errors
        await self._tracker.record(arguments.get("user_id", ""), name, arguments, result)
        payload = json.dumps(result, default=str)
        await self._usage.record(
            user_id=arguments.get("user_id", ""),
            tool=name,
            input_tokens=estimate_tokens(json.dumps(arguments, default=str)),
            output_tokens=estimate_tokens(payload),
            embedding_tokens=usage_end(),
            project=arguments.get("project") or arguments.get("session_id") or None,
            error=failed or "error" in result,
        )
        return result

    async def ingest_source(
        self,
        user_id: str,
        project: str,
        source_id: str | None = None,
        source: str | None = None,
        text: str | None = None,
        metadata: dict[str, Any] | None = None,
        memory_layer: str = "L3",
        importance: float = 0.8,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Ingest one document, from a URL the server fetches or from text the
        caller pushes, tagged with the caller's source id so it can be
        re-synced or retired later as a unit."""
        from src.models.memory_layer import MemoryLayer as ML

        if (source is None) == (text is None):
            raise ValueError("exactly one of source (path or URL) or text is required")

        _, _, _, ingest, _ = await self._get_services(user_id, project)
        meta: dict[str, Any] = dict(metadata or {})
        if source_id:
            meta["source_id"] = source_id

        if source is not None:
            result = await ingest.ingest(
                source=source,
                user_id=user_id,
                memory_layer=ML(memory_layer),
                importance=importance,
                project=project,
                session_id=session_id,
                metadata=meta,
            )
        else:
            # Pushed text has no location of its own, so `source` falls back to
            # the caller's source id. A url in the metadata is preferred when
            # there is one: the caller fetched the document itself, and its
            # address is more useful on the chunk than an opaque id.
            meta_url = (metadata or {}).get("url")
            result = await ingest.ingest_text(
                text=text or "",
                user_id=user_id,
                source=(
                    meta_url
                    if isinstance(meta_url, str) and meta_url.strip()
                    else (source_id or "inline-text")
                ),
                memory_layer=ML(memory_layer),
                importance=importance,
                project=project,
                session_id=session_id,
                metadata=meta,
            )

        return {
            "source_id": source_id,
            "source": result.source,
            "chunk_count": result.chunks_stored,
            "chunks_stored": result.chunks_stored,
            "chunks_failed": result.chunks_failed,
            "total_chunks": result.total_chunks,
        }

    async def deprecate_source(
        self, user_id: str, project: str, source_id: str, reason: str | None = None
    ) -> dict[str, Any]:
        """Deprecate every live item that came from one source id."""
        from src.core.sources import SourceService

        storage, _, _, _, _ = await self._get_services(user_id, project)
        deprecated = await SourceService(storage).deprecate_source(
            user_id=user_id, source_id=source_id, reason=reason
        )
        return {"source_id": source_id, "deprecated": deprecated}

    async def summarize_session(
        self,
        user_id: str,
        project: str,
        session_id: str,
        max_tokens: int = 500,
        focus: str | None = None,
    ) -> dict[str, Any]:
        """Summarize a session's working memory and return the summary.

        The `context_summarize` tool schedules this and returns immediately,
        which is right for an agent compacting its own context in the
        background. A caller that needs the summary *in* the turn it is
        building — a rolling conversation summary about to go into a prompt —
        has nothing to wait on, so this awaits the same work instead.
        """
        from src.core.summarize import SummarizeService

        _, _, store, _, _ = await self._get_services(user_id, project)
        result = await SummarizeService(self._redis, self._postgres, store=store).summarize(
            session_id=session_id,
            user_id=user_id,
            max_tokens=max_tokens,
            focus=focus,
        )
        return {
            "summary": result.summary,
            "keyEntities": [
                entity.model_dump() if hasattr(entity, "model_dump") else entity
                for entity in result.key_entities
            ],
            "tokensSaved": result.tokens_saved,
        }

    async def erase_user(self, user_id: str) -> dict[str, Any]:
        """Hard-delete a user's items from every collection, for erasure requests."""
        from src.core.sources import erase_user as erase

        storage = await self._project_manager.get_l4_storage()
        return {"user_id": user_id, **await erase(storage, user_id)}

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
            result = await self.run_tool(name, arguments)
            return [TextContent(type="text", text=json.dumps(result, default=str))]

        self._register_resources()
        self._register_prompts()

    def _register_resources(self) -> None:
        """Proactive context: clients that speak MCP resources can pull the
        session brief without any tool-call discipline. Identity comes from
        settings.default_user_id (resources carry no user_id argument)."""
        from mcp.types import Resource
        from pydantic import AnyUrl

        @self._server.list_resources()
        async def list_resources() -> list[Resource]:
            return [
                Resource(
                    uri=AnyUrl("context://brief"),
                    name="Session brief",
                    description=(
                        "Token-budgeted session-start digest for the default "
                        "user's active project: identity, project knowledge, "
                        "recent changes, attempts, open tasks."
                    ),
                    mimeType="application/json",
                ),
                Resource(
                    uri=AnyUrl("context://projects"),
                    name="Projects",
                    description="Known project collections and the active project.",
                    mimeType="application/json",
                ),
            ]

        @self._server.read_resource()
        async def read_resource(uri: Any) -> str:
            import json

            user_id = settings.default_user_id
            try:
                if str(uri) == "context://brief":
                    result = await self._dispatch("context_brief", {"user_id": user_id})
                elif str(uri) == "context://projects":
                    from src.core.project import is_index_collection
                    active = await self._project_manager.get_project(user_id)
                    collections = [
                        c for c in await self._default_qdrant.get_all_collections()
                        if c.startswith("ctx_") and not is_index_collection(c)
                    ]
                    result = {"active_project": active, "collections": sorted(collections)}
                else:
                    result = {"error": f"unknown resource {uri}"}
            except Exception as exc:
                logger.exception("Resource read failed for %s", uri)
                result = {"error": str(exc)}
            return json.dumps(result, default=str)

    def _register_prompts(self) -> None:
        """Prompts render the brief/pack as ready-to-inject messages."""
        from mcp.types import (
            GetPromptResult,
            Prompt,
            PromptArgument,
            PromptMessage,
        )

        @self._server.list_prompts()
        async def list_prompts() -> list[Prompt]:
            return [
                Prompt(
                    name="session-start",
                    description=(
                        "Session-start context digest for a project — inject "
                        "at the top of a new conversation."
                    ),
                    arguments=[
                        PromptArgument(
                            name="project",
                            description="Project slug (optional — defaults to the active project)",
                            required=False,
                        ),
                    ],
                ),
                Prompt(
                    name="pack-context",
                    description=(
                        "Assembled, token-budgeted context block for a specific "
                        "task, with provenance markers."
                    ),
                    arguments=[
                        PromptArgument(name="query", description="The task to pack context for", required=True),
                        PromptArgument(name="project", description="Project slug (optional)", required=False),
                        PromptArgument(name="max_tokens", description="Token budget (default 3000)", required=False),
                    ],
                ),
            ]

        @self._server.get_prompt()
        async def get_prompt(name: str, arguments: dict[str, str] | None) -> GetPromptResult:
            import json

            args = arguments or {}
            user_id = settings.default_user_id
            try:
                if name == "session-start":
                    call: dict[str, Any] = {"user_id": user_id}
                    if args.get("project"):
                        call["project"] = args["project"]
                    brief = await self._dispatch("context_brief", call)
                    text = (
                        "Context from Synatyx memory (session brief):\n\n"
                        + json.dumps(brief, indent=2, default=str)
                    )
                elif name == "pack-context":
                    call = {"user_id": user_id, "query": args.get("query", "")}
                    if args.get("project"):
                        call["project"] = args["project"]
                    if args.get("max_tokens"):
                        call["max_tokens"] = int(args["max_tokens"])
                    packed = await self._dispatch("context_pack", call)
                    text = packed.get("rendered", "") or "(no context matched)"
                else:
                    text = f"Unknown prompt: {name}"
            except Exception as exc:
                logger.exception("Prompt %r failed", name)
                text = f"Context unavailable: {exc}"
            return GetPromptResult(
                description=f"Synatyx {name}",
                messages=[
                    PromptMessage(
                        role="user",
                        content=TextContent(type="text", text=text),
                    )
                ],
            )

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

        # context_brief documents session_id as the project slug — treat it as
        # the project when no explicit one is passed, so a brief is always
        # scoped to the caller's project (collection routing AND the Postgres
        # task filter), immune to the shared active-pointer race.
        if (
            name == "context_brief"
            and not (isinstance(args.get("project"), str) and args["project"].strip())
            and isinstance(args.get("session_id"), str)
            and args["session_id"].strip()
        ):
            args["project"] = args["session_id"]

        # ── All other tools ──────────────────────────────────────────────────
        # Normalize the project argument to its canonical slug once — points
        # carry the slug in their payload, so filters must compare slugs, and
        # routing/collection names are slug-based too.
        if isinstance(args.get("project"), str) and args["project"].strip():
            from src.core.project import slugify
            args["project"] = slugify(args["project"])
        else:
            args.pop("project", None)

        # An explicit project argument wins over the active-project pointer:
        # the pointer is one per-user value shared by every concurrent session,
        # so routing by it races when two sessions work in different projects.
        storage, retrieve, store, ingest, suggestion = await self._get_services(
            user_id, args.get("project")
        )
        _warn: dict[str, Any] = (
            {"_project_warning": f"No project set. Detected workspace '{suggestion}'. Call context_set_project to confirm."}
            if suggestion else {}
        )

        if name == "context_brief":
            l4_storage = await self._project_manager.get_l4_storage()
            brief_svc = BriefService(storage, l4_storage, self._postgres)
            slug = await self._project_manager.get_project(user_id)
            # Fall back to the active-pointer slug so open_tasks (Postgres,
            # cross-project by nature) is always filtered to one project.
            brief_result = await brief_svc.brief(
                user_id=user_id,
                project=args.get("project") or slug,
                session_id=args.get("session_id"),
                max_tokens=args.get("max_tokens", 2000),
                recent_days=args.get("recent_days", 7),
            )
            return {
                "project": slug,
                "collection": storage.collection_name,
                **brief_result,
                **_warn,
            }

        elif name == "context_pack":
            pack_svc = await self._get_pack_service(user_id, args.get("project"))
            pack_result = await pack_svc.pack(
                user_id=user_id,
                query=args["query"],
                project=args.get("project"),
                session_id=args.get("session_id"),
                max_tokens=args.get("max_tokens", 3000),
                include_code=args.get("include_code", True),
            )
            return {**pack_result, **_warn}

        elif name == "context_index":
            index_svc, _ = await self._get_index_services(user_id, args.get("project"))
            index_result = await index_svc.index(
                source=args["source"],
                user_id=user_id,
                force=args.get("force", False),
                max_files=args.get("max_files", 500),
            )
            # A fresh index must be visible to the next context_pack call
            self._pack_svc_cache.pop(storage.collection_name, None)
            return {**index_result.to_dict(), **_warn}

        elif name == "context_index_search":
            _, index_search = await self._get_index_services(user_id, args.get("project"))
            hits = await index_search.search(
                query=args["query"],
                user_id=user_id,
                top_k=args.get("top_k", 5),
                language=args.get("language"),
                path_prefix=args.get("path_prefix"),
            )
            return {"query": args["query"], "hits": hits, "count": len(hits), **_warn}

        elif name == "context_index_status":
            index_svc, _ = await self._get_index_services(user_id, args.get("project"))
            return {
                **await index_svc.status(
                    user_id, check_staleness=args.get("check_staleness", True)
                ),
                **_warn,
            }

        elif name == "context_retrieve":
            requested = [MemoryLayer(l) for l in args.get("memory_layers", [])] or list(MemoryLayer)
            top_k = args.get("top_k", 10)

            # Split: L4 always comes from ctx_users; everything else from the project collection
            project_layers = [l for l in requested if l != MemoryLayer.L4]
            include_l4 = MemoryLayer.L4 in requested

            combined_items = []
            suggested_budget: dict = {}

            # Filters go down into the Qdrant payload filter rather than
            # trimming the result set afterwards, which would quietly return
            # fewer than top_k.
            filters = args.get("filters") or {}

            if project_layers:
                proj_result = await retrieve.retrieve(
                    query=args["query"],
                    user_id=user_id,
                    session_id=args.get("session_id"),
                    project=args.get("project"),
                    top_k=top_k,
                    memory_layers=project_layers,
                    metadata_filters=filters or None,
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

            from src.core.citations import annotate as annotate_citation
            from src.core.staleness import annotate_staleness
            dumped_items = [
                annotate_citation(annotate_staleness(i.model_dump())) for i in final_items
            ]

            # Optional 1-hop relation expansion — pull in linked memories
            if args.get("expand_relations") and final_items:
                relations = await self._get_relation_service(user_id, args.get("project"))
                expanded = await relations.expand(
                    user_id=user_id,
                    item_ids=[i.id for i in final_items],
                    max_items=top_k,
                )
                dumped_items.extend(expanded)
                total_tokens += sum(estimate_tokens(e.get("content", "")) for e in expanded)

            retrieve_result: dict[str, Any] = {
                "context_items": dumped_items,
                "total_tokens": total_tokens,
                "suggested_budget": suggested_budget,
                **_warn,
            }

            # Empty results are ambiguous — attach diagnostics so the agent can
            # tell "nothing stored" from "filters/layers missed". Never let the
            # extra counting break the retrieve itself.
            if not final_items:
                try:
                    by_layer: dict[str, int] = {}
                    for _layer in (MemoryLayer.L2, MemoryLayer.L3):
                        by_layer[_layer.value] = await storage.count_items(
                            user_id=user_id, memory_layer=_layer
                        )
                    l4_storage = await self._project_manager.get_l4_storage()
                    by_layer[MemoryLayer.L4.value] = await l4_storage.count_items(
                        user_id=user_id, memory_layer=MemoryLayer.L4
                    )
                    retrieve_result["diagnostics"] = empty_retrieve_diagnostics(
                        total_for_user=sum(by_layer.values()),
                        items_by_layer=by_layer,
                        requested_layers=requested,
                        session_id=args.get("session_id"),
                        project=args.get("project"),
                    )
                except Exception:
                    logger.exception("Retrieve diagnostics failed (non-critical)")

            return retrieve_result

        elif name == "context_store":
            # Batch mode: store several items in one call
            if "items" in args and args["items"]:
                results: list[dict[str, Any]] = []
                for entry in args["items"]:
                    entry_layer = MemoryLayer(entry["memory_layer"])
                    # L4 is user-global — always goes to ctx_users
                    _store = (
                        store if entry_layer != MemoryLayer.L4
                        else (await self._get_l4_services())[1]
                    )
                    batch = await _store.store_batch(
                        [entry], user_id=user_id, session_id=args.get("session_id")
                    )
                    # Same-purpose detection (L1 lives in Redis — no embedding to compare)
                    if settings.relation.detect_enabled and entry_layer != MemoryLayer.L1:
                        for r in batch:
                            if "item_id" in r:
                                detection = await self._detect_alternatives_safe(
                                    user_id, r["item_id"], args.get("project")
                                )
                                if detection["auto_linked"] or detection["suggestions"]:
                                    r.update(detection)
                    results.extend(batch)
                stored = sum(1 for r in results if "error" not in r)
                return {
                    "results": results,
                    "stored": stored,
                    "failed": len(results) - stored,
                    **_warn,
                }

            if "content" not in args or "memory_layer" not in args:
                return {
                    "error": "Provide either 'items' (batch) or 'content' + 'memory_layer' (single)."
                }

            layer = MemoryLayer(args["memory_layer"])
            # L4 is user-global — always goes to ctx_users, not the active project collection
            _store = store if layer != MemoryLayer.L4 else (await self._get_l4_services())[1]
            item_ids, embedded = await _store.store(
                content=args["content"],
                user_id=user_id,
                memory_layer=layer,
                importance=args.get("importance", 0.5),
                session_id=args.get("session_id"),
                metadata=args.get("metadata"),
                confidence=args.get("confidence", 1.0),
                origin=args.get("origin"),
            )
            single_result: dict[str, Any] = {
                "item_id": item_ids[0], "item_ids": item_ids, "embedded": embedded, **_warn
            }
            # Same-purpose detection (L1 lives in Redis — no embedding to compare)
            if settings.relation.detect_enabled and layer != MemoryLayer.L1 and embedded:
                detection = await self._detect_alternatives_safe(
                    user_id, item_ids[0], args.get("project")
                )
                if detection["auto_linked"] or detection["suggestions"]:
                    single_result.update(detection)
            return single_result

        elif name == "context_summarize":
            summarize = SummarizeService(self._redis, self._postgres, store=store)
            # An agent compacting its own context in the background has nothing
            # to wait for; a caller assembling a prompt in this turn needs the
            # summary itself, and scheduling it would hand back nothing usable.
            if args.get("sync"):
                return {
                    **await self.summarize_session(
                        user_id=user_id,
                        project=args.get("project") or "",
                        session_id=args["session_id"],
                        max_tokens=args.get("max_tokens", 500),
                        focus=args.get("focus"),
                    ),
                    **_warn,
                }
            await summarize.summarize_async(
                session_id=args["session_id"],
                user_id=user_id,
                max_tokens=args.get("max_tokens", 500),
                focus=args.get("focus"),
            )
            return {"status": "summarization_scheduled", **_warn}

        elif name == "context_deprecate_source":
            return {
                **await self.deprecate_source(
                    user_id=user_id,
                    project=args.get("project") or "",
                    source_id=args["source_id"],
                    reason=args.get("reason"),
                ),
                **_warn,
            }

        elif name == "context_erase_user":
            return {**await self.erase_user(args["target_user_id"]), **_warn}

        elif name == "context_score":
            from src.models.context import ContextItem
            items = [ContextItem(**i) for i in args["items"]]
            scored, dropped = score_items(items, args["query"])
            return {
                "scored_items": [i.model_dump() for i in scored],
                "dropped_items": [i.model_dump() for i in dropped],
            }

        elif name == "context_ingest":
            # Delegates to the same method the REST transport calls, so the
            # two cannot drift into ingesting differently.
            ingested = await self.ingest_source(
                user_id=user_id,
                project=args.get("project") or "",
                source_id=args.get("source_id"),
                source=args.get("source"),
                text=args.get("text"),
                metadata={
                    key: args[key]
                    for key in ("url", "title", "locale")
                    if args.get(key) is not None
                },
                memory_layer=args.get("memory_layer", "L3"),
                importance=float(args.get("importance", 0.8)),
                session_id=args.get("session_id"),
            )
            return {**ingested, **_warn}

        elif name == "context_checkpoint":
            item_ids, embedded = await store.checkpoint(
                name=args["name"],
                content=args["content"],
                user_id=user_id,
                project=args.get("project"),
                session_id=args.get("session_id"),
            )
            return {"item_id": item_ids[0], "item_ids": item_ids, "embedded": embedded, "checkpoint_name": args["name"], **_warn}

        elif name == "context_deprecate":
            item_id = args["item_id"]
            superseded_by = args.get("superseded_by")
            # L4 items live in ctx_users — fall back if not in the project collection
            _dep_store = store
            if await storage.get_by_id(item_id) is None:
                l4_storage, l4_store, _ = await self._get_l4_services()
                if await l4_storage.get_by_id(item_id) is not None:
                    _dep_store = l4_store
            await _dep_store.deprecate(
                item_id=item_id,
                user_id=user_id,
                reason=args.get("reason"),
            )
            result: dict[str, Any] = {"deprecated": True, "item_id": item_id}
            if superseded_by:
                relations = await self._get_relation_service(user_id, args.get("project"))
                edge, _created = await relations.relate(
                    user_id=user_id,
                    source_id=superseded_by,
                    target_id=item_id,
                    relation_type="supersedes",
                )
                result["superseded_by"] = superseded_by
                result["relation_id"] = edge.id
            return result

        elif name == "context_relate":
            relations = await self._get_relation_service(user_id, args.get("project"))
            edge, created = await relations.relate(
                user_id=user_id,
                source_id=args["source_id"],
                target_id=args["target_id"],
                relation_type=args.get("relation_type", "related_to"),
                project=args.get("project"),
                metadata=args.get("metadata"),
            )
            return {
                "relation_id": edge.id,
                "source_id": edge.source_item_id,
                "target_id": edge.target_item_id,
                "relation_type": edge.relation_type,
                "created": created,
            }

        elif name == "context_unrelate":
            relations = await self._get_relation_service(user_id, args.get("project"))
            deleted = await relations.unrelate(
                user_id=user_id,
                relation_id=args.get("relation_id"),
                source_id=args.get("source_id"),
                target_id=args.get("target_id"),
                relation_type=args.get("relation_type"),
            )
            return {"deleted": deleted}

        elif name == "context_related":
            relations = await self._get_relation_service(user_id, args.get("project"))
            edges, neighbors = await relations.related(
                user_id=user_id,
                item_id=args["item_id"],
                relation_type=args.get("relation_type"),
                direction=args.get("direction", "both"),
            )
            return {
                "item_id": args["item_id"],
                "relations": [
                    {
                        "relation_id": e.id,
                        "source_id": e.source_item_id,
                        "target_id": e.target_item_id,
                        "relation_type": e.relation_type,
                        "created_at": e.created_at.isoformat(),
                    }
                    for e in edges
                ],
                "items": {item_id: item.model_dump() for item_id, item in neighbors.items()},
                "count": len(edges),
            }

        elif name == "context_get":
            relations = await self._get_relation_service(user_id, args.get("project"))
            fetched = await relations.get_item(args["item_id"], user_id)
            if fetched is None:
                return {"error": f"Item {args['item_id']!r} not found"}
            return {"item": fetched.model_dump()}

        elif name == "context_alternatives":
            alternatives_svc = await self._get_alternatives_service(user_id, args.get("project"))
            groups = await alternatives_svc.alternatives(
                user_id=user_id,
                query=args["query"],
                top_k=args.get("top_k", 5),
            )
            return {"query": args["query"], "groups": groups, "count": len(groups), **_warn}

        elif name == "context_visualize":
            from src.core.visualize import render_mermaid
            from src.models.memory_layer import MemoryLayer as ML
            layer_str = args.get("memory_layer")
            graph_layer = ML(layer_str) if layer_str else None
            # L4 lives in ctx_users — route there when the filter is explicitly L4
            _viz_storage = (
                (await self._get_l4_services())[0] if graph_layer == ML.L4 else storage
            )
            graph_items = await _viz_storage.list_items(
                user_id=user_id,
                memory_layer=graph_layer,
                include_deprecated=args.get("include_deprecated", True),
                project=args.get("project"),
                limit=args.get("limit", 50),
            )
            relations = await self._get_relation_service(user_id, args.get("project"))
            edges = await self._postgres.relation_list(
                user_id=user_id,
                item_ids=[i.id for i in graph_items],
                limit=500,
            )
            # Edges may reach items outside the listed set (other layers, the
            # shared L4 collection, deprecated supersedes targets) — hydrate
            # those endpoints so their edges render instead of being dropped.
            known_ids = {i.id for i in graph_items}
            for edge in edges:
                for endpoint in (edge.source_item_id, edge.target_item_id):
                    if endpoint in known_ids:
                        continue
                    known_ids.add(endpoint)
                    try:
                        neighbor = await relations.get_item(endpoint, user_id)
                    except PermissionError:
                        continue
                    if neighbor is not None:
                        graph_items.append(neighbor)
            mermaid, node_count, edge_count = render_mermaid(
                graph_items,
                edges,
                direction=args.get("direction", "LR"),
                relations_only=args.get("relations_only", False),
            )
            return {
                "mermaid": mermaid,
                "node_count": node_count,
                "edge_count": edge_count,
                **_warn,
            }

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

        elif name == "context_skill_store":
            svc = await self._get_skill_service(user_id, args.get("project"))
            skill = await svc.store(
                name=args["name"],
                description=args["description"],
                content=args["content"],
                user_id=user_id,
                project=args.get("project"),
                frontmatter=args.get("frontmatter"),
            )
            return {
                "skill_id": skill.id,
                "name": skill.name,
                "slug": skill.slug,
                "project": skill.project,
                "created_at": skill.created_at.isoformat(),
            }

        elif name == "context_skill_find":
            svc = await self._get_skill_service(user_id, args.get("project"))
            results = await svc.find(
                query=args["query"],
                user_id=user_id,
                project=args.get("project"),
                top_k=args.get("top_k", 3),
            )
            return {"skills": results, "count": len(results)}

        elif name == "context_skill_get":
            svc = await self._get_skill_service(user_id, args.get("project"))
            skill = await svc.get(
                name=args["name"],
                user_id=user_id,
                project=args.get("project"),
            )
            if not skill:
                return {"error": f"Skill {args['name']!r} not found"}
            return {
                "name": skill.name,
                "slug": skill.slug,
                "description": skill.description,
                "content": skill.content,
                "frontmatter": skill.frontmatter,
                "project": skill.project,
            }

        elif name == "context_skill_list":
            svc = await self._get_skill_service(user_id, args.get("project"))
            skills = await svc.list_skills(
                user_id=user_id,
                project=args.get("project"),
                limit=args.get("limit", 50),
            )
            return {
                "skills": [
                    {"name": s.name, "slug": s.slug, "description": s.description, "project": s.project}
                    for s in skills
                ],
                "count": len(skills),
            }

        elif name == "context_skill_delete":
            svc = await self._get_skill_service(user_id, args.get("project"))
            deleted = await svc.delete(name=args["name"], user_id=user_id)
            if not deleted:
                return {"error": f"Skill {args['name']!r} not found"}
            return {"deleted": True, "name": args["name"]}

        elif name == "context_consolidate":
            from src.core.consolidate import Consolidator
            consolidator = Consolidator(
                qdrant=storage, postgres=self._postgres, settings=settings.consolidation
            )
            # Manual trigger runs on the active project's collection only —
            # the GC daemon handles the all-collections background sweep.
            stats = await consolidator._process_collection(
                storage, storage.collection_name, {"merged_clusters": 0}
            )
            return {
                "collection": storage.collection_name,
                **stats,
                "similarity_threshold": settings.consolidation.similarity_threshold,
                "min_cluster_size": settings.consolidation.min_cluster_size,
                **_warn,
            }

        elif name == "context_gc_stats":
            # _get_services returns (storage, retrieve, store, ingest, suggestion)
            qdrant = storage
            from src.config import settings as _settings
            from src.core.gc import GarbageCollector, _IMMUNE_LAYERS, _IMMUNE_TYPE
            from datetime import datetime, timedelta, timezone

            gc = GarbageCollector(qdrant=qdrant, postgres=self._postgres, settings=_settings.gc)
            now = datetime.now(timezone.utc)
            warn_threshold = timedelta(days=14)

            all_items, _ = await qdrant.scan_all_items(include_deprecated=True, limit=1000)
            total = len(all_items)
            protected = expiring_soon = deprecated = pending_hard_delete = 0

            for item in all_items:
                if item.get("is_deprecated"):
                    deprecated += 1
                    dep_raw = item.get("deprecated_at")
                    if dep_raw:
                        dep_at = datetime.fromisoformat(dep_raw)
                        if dep_at.tzinfo is None:
                            dep_at = dep_at.replace(tzinfo=timezone.utc)
                        if (now - dep_at).days >= _settings.gc.grace_period_days:
                            pending_hard_delete += 1
                    continue

                if gc._is_immune(item):
                    protected += 1
                    continue

                base_ttl = gc._get_base_ttl(item.get("memory_layer", ""))
                if base_ttl is None:
                    protected += 1
                    continue

                effective_ttl = gc.effective_ttl(base_ttl, item)
                last_raw = item.get("last_accessed_at") or item.get("created_at")
                if last_raw:
                    last = datetime.fromisoformat(last_raw)
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    remaining = timedelta(days=effective_ttl) - (now - last)
                    if timedelta(0) < remaining <= warn_threshold:
                        expiring_soon += 1

            return {
                "total_items": total,
                "protected": protected,
                "expiring_soon_14d": expiring_soon,
                "already_deprecated": deprecated,
                "pending_hard_delete": pending_hard_delete,
                "gc_enabled": _settings.gc.enabled,
                "l2_base_ttl_days": _settings.gc.l2_base_ttl_days,
                "l3_base_ttl_days": _settings.gc.l3_base_ttl_days,
                "grace_period_days": _settings.gc.grace_period_days,
            }

        elif name == "context_usage":
            group_by = args.get("group_by", "tool")
            if group_by not in ("tool", "project", "day"):
                raise ValueError("group_by must be one of: tool, project, day")
            return await self._usage.stats(
                user_id=user_id or None,
                project=args.get("project"),
                days=int(args.get("days", 30)),
                group_by=group_by,
            )

        raise ValueError(f"Unknown tool: {name}")

    async def _store_for_user(self, user_id: str):
        """StoreService bound to the user's active project — for trace compaction."""
        _, _, store, _, _ = await self._get_services(user_id)
        return store

    async def run_tracking_loop(self) -> None:
        """Periodically compact idle session traces into L2 memories.

        Runs for the lifetime of the server process (stdio or HTTP). Cancelled
        on shutdown; every pass is exception-isolated.
        """
        import asyncio

        interval = settings.tracking.compact_interval_seconds
        while True:
            await asyncio.sleep(interval)
            try:
                await self._tracker.compact_idle(self._store_for_user)
            except Exception:
                logger.exception("Tracking compaction pass failed")

    async def run_stdio(self) -> None:
        """Run the MCP server over stdio (for OpenClaw / Claude Desktop)."""
        import asyncio

        tracking_task = asyncio.create_task(self.run_tracking_loop())
        try:
            async with stdio_server() as (read_stream, write_stream):
                await self._server.run(read_stream, write_stream, self._server.create_initialization_options())
        finally:
            tracking_task.cancel()

