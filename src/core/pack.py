from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.core.budget import BudgetEntry, SectionBudgeter, estimate_tokens
from src.core.embedder import get_embedder
from src.models.context import ContextItem
from src.models.memory_layer import MemoryLayer
from src.storage.postgres import PostgresStorage
from src.storage.qdrant import QdrantStorage

if TYPE_CHECKING:
    from src.core.index import IndexSearchService
    from src.core.relation import RelationService
    from src.core.retrieve import RetrieveService
    from src.core.skill import SkillService

logger = logging.getLogger(__name__)

# Fraction of max_tokens per section. Memories dominate — they're what the
# query actually matched; the rest is orientation and guard-rails. Weights are
# renormalized over the sections that are present (e.g. no code index), so no
# budget is silently lost.
PACK_WEIGHTS: dict[str, float] = {
    "identity": 0.10,      # L4 — who the user is, how they work
    "memories": 0.38,      # L2/L3 hybrid retrieval + 1-hop relations
    "checkpoints": 0.12,   # pinned decisions touching the query
    "attempts": 0.08,      # dead ends relevant to the query — don't repeat them
    "open_tasks": 0.08,
    "skills": 0.10,        # matching skills — names + descriptions
    "code": 0.14,          # persistent code/doc index hits
}

DEFAULT_PACK_TOKENS = 3000
_MEMORY_TOP_K = 12
_RELATION_CAP = 6
_ATTEMPT_SCAN_K = 20
_IDENTITY_TOP_K = 5
_CHECKPOINT_TOP_K = 5
_SKILL_TOP_K = 3
_CODE_TOP_K = 5

_SECTION_TITLES: dict[str, str] = {
    "identity": "Who you're working with",
    "memories": "Relevant memories",
    "checkpoints": "Pinned checkpoints",
    "attempts": "Known dead ends",
    "open_tasks": "Open tasks",
    "skills": "Matching skills",
    "code": "Code (from index)",
}


def _created_at(item: ContextItem) -> datetime:
    ts = item.created_at
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _dump_pack(item: ContextItem, score: float | None = None) -> dict[str, Any]:
    """Serialize one memory for a pack section — brief's `_dump` shape plus
    the retrieval score."""
    dumped: dict[str, Any] = {
        "id": item.id,
        "content": item.content,
        "memory_layer": item.memory_layer.value,
        "importance": item.importance,
        "is_pinned": item.is_pinned,
        "origin": item.metadata.get("origin"),
        "created_at": _created_at(item).isoformat(),
    }
    if score is not None:
        dumped["score"] = round(score, 4)
    if item.metadata.get("file_hashes"):
        from src.core.staleness import check_stale_files
        stale = check_stale_files(item.metadata)
        if stale:
            dumped["possibly_stale"] = True
            dumped["stale_files"] = stale
    return dumped


def _entry_for_item(item: ContextItem, score: float | None = None) -> BudgetEntry:
    return BudgetEntry(
        tokens=item.token_estimate,
        payload=(lambda i=item, s=score: _dump_pack(i, s)),
        raw_content=item.content,
    )


def _entry_for_dict(payload: dict[str, Any], content: str) -> BudgetEntry:
    return BudgetEntry(
        tokens=estimate_tokens(content),
        payload=payload,
        raw_content=content,
    )


class PackService:
    """Assemble one prompt-ready, token-budgeted context block for a task.

    Where `context_brief` scrolls and sorts (session orientation), pack is
    query-driven: every section is selected by relevance to the task at hand,
    packed with `SectionBudgeter` (spillover on — unspent budget flows to the
    fuller sections), and rendered to markdown with provenance markers.
    """

    def __init__(
        self,
        retrieve: RetrieveService,
        project_storage: QdrantStorage,
        l4_storage: QdrantStorage,
        postgres: PostgresStorage,
        relations: RelationService,
        skills: SkillService,
        index_search: IndexSearchService | None = None,
        embedder: Any | None = None,
    ) -> None:
        self._retrieve = retrieve
        self._project_storage = project_storage
        self._l4_storage = l4_storage
        self._postgres = postgres
        self._relations = relations
        self._skills = skills
        self._index_search = index_search
        self._embedder = embedder or get_embedder()

    async def pack(
        self,
        user_id: str,
        query: str,
        project: str | None = None,
        session_id: str | None = None,
        max_tokens: int = DEFAULT_PACK_TOKENS,
        include_code: bool = True,
        include_skill_body: bool = True,
    ) -> dict[str, Any]:
        vector = await self._embedder.embed(query)
        errors: list[str] = []

        async def guarded(name: str, coro: Any) -> Any:
            try:
                return await coro
            except Exception as exc:
                logger.warning("Pack: %s section failed — omitting: %s", name, exc)
                errors.append(name)
                return None

        with_code = include_code and self._index_search is not None
        gathered = await asyncio.gather(
            guarded("identity", self._identity(user_id, vector)),
            guarded("memories", self._memories(user_id, query, vector, session_id, project)),
            guarded("checkpoints", self._checkpoints(user_id, vector, project)),
            guarded("attempts", self._attempts(user_id, vector, project)),
            guarded("open_tasks", self._open_tasks(user_id, query, project)),
            guarded("skills", self._skills_section(user_id, query, project)),
            guarded("code", self._code(user_id, query)) if with_code else _none(),
        )
        identity, memories, checkpoints, attempts, open_tasks, skills, code = (
            g if g is not None else [] for g in gathered
        )

        # Cross-section dedup by item id — memories win, then checkpoints,
        # attempts, identity (relation-expanded entries carry ids too).
        seen: set[str] = set()

        def dedup(entries: list[BudgetEntry]) -> list[BudgetEntry]:
            kept: list[BudgetEntry] = []
            for e in entries:
                eid = e.materialize().get("id") if not callable(e.payload) else None
                # avoid materializing lazy payloads just for dedup — lazy
                # entries stash their id on the entry via _pack_item_id
                eid = getattr(e, "_pack_item_id", eid)
                if eid and eid in seen:
                    continue
                if eid:
                    seen.add(eid)
                kept.append(e)
            return kept

        sections: dict[str, list[BudgetEntry]] = {
            "identity": identity,
            "memories": memories,
            "checkpoints": checkpoints,
            "attempts": attempts,
            "open_tasks": open_tasks,
            "skills": skills,
        }
        # dedup in priority order, then restore canonical section order
        for name in ("memories", "checkpoints", "attempts", "identity"):
            sections[name] = dedup(sections[name])
        if with_code:
            sections["code"] = code
        sections = {k: sections[k] for k in PACK_WEIGHTS if k in sections}
        # drop empty sections from budgeting so their weight is renormalized away
        active = {k: v for k, v in sections.items() if v}

        budgeter = SectionBudgeter(max_tokens, PACK_WEIGHTS)
        results = budgeter.pack(active, spillover=True) if active else {}

        packed_sections: dict[str, list[dict[str, Any]]] = {
            name: (results[name].entries if name in results else [])
            for name in PACK_WEIGHTS
        }
        token_estimate = sum(r.used for r in results.values())

        # Skill-body promotion: if there's leftover budget, replace the top
        # skill's entry with its full body.
        if include_skill_body and packed_sections.get("skills"):
            leftover = max_tokens - token_estimate
            top = packed_sections["skills"][0]
            body = top.get("_body")
            if body:
                body_tokens = estimate_tokens(body) - estimate_tokens(top.get("description", ""))
                if 0 < body_tokens <= leftover:
                    top["content"] = body
                    top["body_included"] = True
                    token_estimate += body_tokens
            for entry in packed_sections["skills"]:
                entry.pop("_body", None)

        budget_report = {
            name: {"allocated": r.allocated, "used": r.used, "overflow": r.overflow}
            for name, r in results.items()
        }

        out: dict[str, Any] = {
            "query": query,
            "sections": packed_sections,
            "rendered": render_pack(query, packed_sections, project, token_estimate),
            "token_estimate": token_estimate,
            "max_tokens": max_tokens,
            "budget": budget_report,
        }
        if errors:
            out["errors"] = [f"{name} section unavailable" for name in errors]
        if not any(packed_sections.values()):
            out["diagnostics"] = await self._empty_diagnostics(user_id, session_id, project)
        return out

    # ------------------------------------------------------------------
    # Section builders — each returns list[BudgetEntry]
    # ------------------------------------------------------------------

    async def _identity(self, user_id: str, vector: list[float]) -> list[BudgetEntry]:
        hits = await self._l4_storage.search(
            query_vector=vector,
            user_id=user_id,
            top_k=_IDENTITY_TOP_K,
            memory_layer=MemoryLayer.L4,
        )
        items: list[ContextItem] = list(hits)
        if len(items) < 2:
            extra = await self._l4_storage.list_items(
                user_id=user_id, memory_layer=MemoryLayer.L4, limit=20
            )
            have = {i.id for i in items}
            extra.sort(key=lambda i: i.importance, reverse=True)
            items.extend(i for i in extra if i.id not in have)
            items = items[:_IDENTITY_TOP_K]
        return [self._tagged_entry(i, getattr(i, "score", None)) for i in items]

    async def _memories(
        self,
        user_id: str,
        query: str,
        vector: list[float],
        session_id: str | None,
        project: str | None,
    ) -> list[BudgetEntry]:
        result = await self._retrieve.retrieve(
            query=query,
            user_id=user_id,
            session_id=session_id,
            project=project,
            top_k=_MEMORY_TOP_K,
            memory_layers=[MemoryLayer.L2, MemoryLayer.L3],
            query_embedding=vector,
        )
        entries = [self._tagged_entry(i, i.score) for i in result.context_items]

        if result.context_items:
            try:
                expanded = await self._relations.expand(
                    user_id, [i.id for i in result.context_items], max_items=_RELATION_CAP
                )
            except Exception as exc:
                logger.warning("Pack: relation expansion failed — skipping: %s", exc)
                expanded = []
            for dumped in expanded:
                content = dumped.get("content", "")
                layer = dumped.get("memory_layer")
                payload = {
                    "id": dumped.get("id"),
                    "content": content,
                    "memory_layer": layer.value if isinstance(layer, MemoryLayer) else layer,
                    "importance": dumped.get("importance"),
                    "is_pinned": dumped.get("is_pinned", False),
                    "origin": (dumped.get("metadata") or {}).get("origin"),
                    "via_relation": dumped.get("via_relation"),
                }
                entry = _entry_for_dict(payload, content)
                entry._pack_item_id = payload["id"]  # type: ignore[attr-defined]
                entries.append(entry)
        return entries

    async def _checkpoints(
        self, user_id: str, vector: list[float], project: str | None
    ) -> list[BudgetEntry]:
        hits = await self._project_storage.search(
            query_vector=vector,
            user_id=user_id,
            top_k=_CHECKPOINT_TOP_K,
            memory_layer=MemoryLayer.L3,
            project=project,
            pinned_only=True,
        )
        return [self._tagged_entry(i, i.score) for i in hits]

    async def _attempts(
        self, user_id: str, vector: list[float], project: str | None
    ) -> list[BudgetEntry]:
        hits = await self._project_storage.search(
            query_vector=vector,
            user_id=user_id,
            top_k=_ATTEMPT_SCAN_K,
            memory_layer=MemoryLayer.L2,
            project=project,
        )
        attempts = [i for i in hits if i.metadata.get("type") == "attempt"]
        return [self._tagged_entry(i, i.score) for i in attempts]

    async def _open_tasks(
        self, user_id: str, query: str, project: str | None
    ) -> list[BudgetEntry]:
        from src.core.bm25 import tokenize
        from src.models.task import TaskStatus

        in_progress = await self._postgres.task_list(
            user_id=user_id, status=TaskStatus.IN_PROGRESS, project=project
        )
        pending = await self._postgres.task_list(
            user_id=user_id, status=TaskStatus.PENDING, project=project
        )
        query_tokens = set(tokenize(query))

        def overlap(task: Any) -> int:
            return len(query_tokens & set(tokenize(f"{task.title} {task.description}")))

        ranked = sorted(in_progress, key=overlap, reverse=True) + sorted(
            pending, key=overlap, reverse=True
        )
        entries = []
        for t in ranked:
            desc = (t.description or "")[:200]
            content = f"[{t.status.value if hasattr(t.status, 'value') else t.status}] {t.title}: {desc}"
            payload = {
                "id": t.id,
                "title": t.title,
                "status": t.status.value if hasattr(t.status, "value") else t.status,
                "priority": t.priority.value if hasattr(t.priority, "value") else t.priority,
                "description": desc,
            }
            entries.append(_entry_for_dict(payload, content))
        return entries

    async def _skills_section(
        self, user_id: str, query: str, project: str | None
    ) -> list[BudgetEntry]:
        found = await self._skills.find(query, user_id, project=project, top_k=_SKILL_TOP_K)
        entries = []
        for skill in found:
            summary = f"{skill['name']} — {skill['description']}"
            payload = {
                "name": skill["name"],
                "description": skill["description"],
                "score": round(skill.get("score", 0.0), 4),
                "content": summary,
                "_body": skill.get("content"),
            }
            entries.append(_entry_for_dict(payload, summary))
        return entries

    async def _code(self, user_id: str, query: str) -> list[BudgetEntry]:
        assert self._index_search is not None
        hits = await self._index_search.search(query, user_id, top_k=_CODE_TOP_K)
        entries = []
        for hit in hits:
            content = hit.get("content", "")
            entries.append(_entry_for_dict(dict(hit), content))
        return entries

    # ------------------------------------------------------------------

    def _tagged_entry(self, item: ContextItem, score: float | None) -> BudgetEntry:
        entry = _entry_for_item(item, score)
        entry._pack_item_id = item.id  # type: ignore[attr-defined]
        return entry

    async def _empty_diagnostics(
        self, user_id: str, session_id: str | None, project: str | None
    ) -> dict[str, Any]:
        from src.core.retrieve import empty_retrieve_diagnostics

        try:
            by_layer: dict[str, int] = {}
            for layer in (MemoryLayer.L2, MemoryLayer.L3):
                by_layer[layer.value] = await self._project_storage.count_items(
                    user_id=user_id, memory_layer=layer, project=project
                )
            by_layer[MemoryLayer.L4.value] = await self._l4_storage.count_items(
                user_id=user_id, memory_layer=MemoryLayer.L4
            )
            total = sum(by_layer.values())
        except Exception:
            by_layer, total = {}, 0
        return empty_retrieve_diagnostics(
            total_for_user=total,
            items_by_layer=by_layer,
            requested_layers=[MemoryLayer.L2, MemoryLayer.L3, MemoryLayer.L4],
            session_id=session_id,
            project=project,
        )


async def _none() -> None:
    return None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _provenance_suffix(entry: dict[str, Any]) -> str:
    parts = []
    if entry.get("origin"):
        parts.append(f"[{entry['origin']}]")
    if entry.get("possibly_stale"):
        parts.append(f"[STALE: {', '.join(entry.get('stale_files', []))}]")
    return (" " + " ".join(parts)) if parts else ""


def _layer_tag(entry: dict[str, Any]) -> str:
    layer = entry.get("memory_layer")
    if not layer:
        return ""
    return f"[{layer} · pinned] " if entry.get("is_pinned") else f"[{layer}] "


def render_pack(
    query: str,
    sections: dict[str, list[dict[str, Any]]],
    project: str | None,
    token_estimate: int,
) -> str:
    """Render packed sections to a prompt-ready markdown block.

    Empty sections are omitted; every item carries provenance markers so the
    consuming agent can weigh trust (user-stated vs inferred vs ingested) and
    spot stale facts."""
    today = datetime.now(timezone.utc).date().isoformat()
    header = (
        f"<!-- synatyx context_pack | project: {project or 'active'} "
        f"| ~{token_estimate} tokens | {today} -->"
    )
    lines = [header, f'# Context for: "{query}"']

    if not any(sections.values()):
        lines.append("")
        lines.append("(no stored context matched this query)")
        return "\n".join(lines)

    for name, entries in sections.items():
        if not entries:
            continue
        lines.append("")
        lines.append(f"## {_SECTION_TITLES.get(name, name)}")
        for entry in entries:
            if name == "open_tasks":
                lines.append(
                    f"- [{entry.get('status')}] {entry.get('title')}"
                    + (f" — {entry['description']}" if entry.get("description") else "")
                )
            elif name == "skills":
                suffix = "" if entry.get("body_included") else " (body omitted — fetch with context_skill_get)"
                if entry.get("body_included"):
                    lines.append(f"- **{entry.get('name')}**:")
                    lines.append("")
                    lines.append(entry.get("content", ""))
                else:
                    lines.append(f"- {entry.get('content', '')}{suffix}")
            elif name == "code":
                loc = f"{entry.get('path')}:{entry.get('line_start')}"
                sym = f" `{entry['symbol']}`" if entry.get("symbol") else ""
                stale = " [STALE]" if entry.get("possibly_stale") else ""
                lines.append(f"- {loc}{sym} ({entry.get('kind', 'chunk')}){stale}")
                snippet = entry.get("content", "")
                if snippet:
                    lang = entry.get("language", "")
                    lines.append(f"  ```{lang}")
                    lines.extend(f"  {l}" for l in snippet.splitlines())
                    lines.append("  ```")
            else:
                content = str(entry.get("content", "")).replace("\n", " ").strip()
                lines.append(f"- {_layer_tag(entry)}{content}{_provenance_suffix(entry)}")
                via = entry.get("via_relation")
                if via:
                    lines.append(f"  ↳ {via.get('relation_type', 'related_to')} (linked memory)")

    return "\n".join(lines)
