from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.pack import PackService, render_pack
from src.core.retrieve import RetrieveResult
from src.models.context import ContextItem, ScoredContextItem
from src.models.memory_layer import MemoryLayer
from src.models.task import Task, TaskStatus


class FakeEmbedder:
    async def embed(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


def _scored(
    content: str,
    layer: MemoryLayer = MemoryLayer.L3,
    user_id: str = "u1",
    score: float = 0.9,
    **overrides: Any,
) -> ScoredContextItem:
    base: dict[str, Any] = {
        "user_id": user_id,
        "content": content,
        "memory_layer": layer,
        "score": score,
        "created_at": datetime.now(UTC) - timedelta(days=1),
    }
    base.update(overrides)
    return ScoredContextItem(**base)


class FakeRetrieve:
    def __init__(self, items: list[ScoredContextItem]) -> None:
        self._items = items
        self.last_kwargs: dict[str, Any] = {}

    async def retrieve(self, **kwargs: Any) -> RetrieveResult:
        self.last_kwargs = kwargs
        return RetrieveResult(
            context_items=self._items,
            total_tokens=sum(i.token_estimate for i in self._items),
            suggested_budget={},
        )


class FakePackStorage:
    """search()/list_items()/count_items() over a canned item list."""

    def __init__(self, items: list[ContextItem]) -> None:
        self._items = items

    async def search(
        self,
        query_vector: list[float],
        user_id: str,
        top_k: int = 10,
        memory_layer: MemoryLayer | None = None,
        project: str | None = None,
        pinned_only: bool = False,
        **kwargs: Any,
    ) -> list[ScoredContextItem]:
        out = []
        for item in self._items:
            if item.user_id != user_id or item.is_deprecated:
                continue
            if memory_layer and item.memory_layer != memory_layer:
                continue
            if pinned_only and not item.is_pinned:
                continue
            out.append(item)
        return out[:top_k]

    async def list_items(
        self, user_id: str, memory_layer=None, limit=50, **kw
    ) -> list[ContextItem]:
        return [
            i for i in self._items
            if i.user_id == user_id and (memory_layer is None or i.memory_layer == memory_layer)
        ][:limit]

    async def count_items(self, user_id: str, memory_layer=None, project=None, **kw) -> int:
        return len(await self.list_items(user_id, memory_layer))


class FakeRelations:
    def __init__(self, expanded: list[dict[str, Any]] | None = None) -> None:
        self._expanded = expanded or []

    async def expand(self, user_id: str, item_ids: list[str], max_items: int = 10):
        return self._expanded[:max_items]


class FakeSkills:
    def __init__(self, skills: list[dict[str, Any]] | None = None) -> None:
        self._skills = skills or []

    async def find(self, query: str, user_id: str, project=None, top_k: int = 3):
        return self._skills[:top_k]


class FakePostgres:
    def __init__(self, tasks: list[Task] | None = None, fail: bool = False) -> None:
        self.tasks = tasks or []
        self.fail = fail

    async def task_list(self, user_id, status=None, priority=None, project=None, limit=50):
        if self.fail:
            raise RuntimeError("postgres down")
        out = [t for t in self.tasks if t.user_id == user_id]
        if status:
            out = [t for t in out if t.status == status]
        return out[:limit]


class FakeIndexSearch:
    def __init__(self, hits: list[dict[str, Any]] | None = None) -> None:
        self._hits = hits or []

    async def search(self, query, user_id, top_k=5, **kw):
        return self._hits[:top_k]


def _service(
    memories: list[ScoredContextItem] | None = None,
    project_items: list[ContextItem] | None = None,
    l4_items: list[ContextItem] | None = None,
    tasks: list[Task] | None = None,
    skills: list[dict[str, Any]] | None = None,
    expanded: list[dict[str, Any]] | None = None,
    index_hits: list[dict[str, Any]] | None = None,
    postgres_fail: bool = False,
) -> PackService:
    return PackService(
        retrieve=FakeRetrieve(memories or []),  # type: ignore[arg-type]
        project_storage=FakePackStorage(project_items or []),  # type: ignore[arg-type]
        l4_storage=FakePackStorage(l4_items or []),  # type: ignore[arg-type]
        postgres=FakePostgres(tasks, fail=postgres_fail),  # type: ignore[arg-type]
        relations=FakeRelations(expanded),  # type: ignore[arg-type]
        skills=FakeSkills(skills),  # type: ignore[arg-type]
        index_search=FakeIndexSearch(index_hits) if index_hits is not None else None,
        embedder=FakeEmbedder(),
    )


# ── sections ─────────────────────────────────────────────────────────────────

async def test_pack_sections_populated() -> None:
    svc = _service(
        memories=[_scored("Qdrant runs on port 6333", metadata={"origin": "user-stated"})],
        l4_items=[_scored("prefers clean commits", MemoryLayer.L4)],
        project_items=[
            _scored("big decision", is_pinned=True),
            _scored("tried X, failed", MemoryLayer.L2, metadata={"type": "attempt"}),
        ],
        tasks=[Task(user_id="u1", title="finish docs", status=TaskStatus.IN_PROGRESS)],
        skills=[
            {
                "name": "reviewer",
                "description": "reviews code",
                "content": "full body",
                "score": 0.8,
            }
        ],
    )
    result = await svc.pack("u1", "how is qdrant configured")

    assert [e["content"] for e in result["sections"]["memories"]] == ["Qdrant runs on port 6333"]
    assert result["sections"]["identity"][0]["content"] == "prefers clean commits"
    assert result["sections"]["checkpoints"][0]["content"] == "big decision"
    assert result["sections"]["attempts"][0]["content"] == "tried X, failed"
    assert result["sections"]["open_tasks"][0]["title"] == "finish docs"
    assert result["sections"]["skills"][0]["name"] == "reviewer"
    assert result["token_estimate"] <= result["max_tokens"]


async def test_pack_rendered_has_provenance_markers() -> None:
    svc = _service(
        memories=[_scored("uses Stripe webhooks", metadata={"origin": "user-stated"})],
    )
    result = await svc.pack("u1", "payments")
    assert "[user-stated]" in result["rendered"]
    assert '# Context for: "payments"' in result["rendered"]
    assert "Relevant memories" in result["rendered"]
    # empty sections are omitted from the rendered block
    assert "Open tasks" not in result["rendered"]


async def test_pack_cross_section_dedup() -> None:
    pinned = _scored("pinned decision", is_pinned=True)
    svc = _service(
        memories=[pinned],
        project_items=[pinned],  # would also surface via checkpoints
    )
    result = await svc.pack("u1", "decision")
    all_ids = [
        e["id"]
        for section in ("memories", "checkpoints")
        for e in result["sections"][section]
    ]
    assert len(all_ids) == len(set(all_ids)) == 1


async def test_pack_relation_expansion_included() -> None:
    anchor = _scored("payments use Stripe")
    svc = _service(
        memories=[anchor],
        expanded=[{
            "id": "rel-1",
            "content": "webhook secret rotates monthly",
            "memory_layer": MemoryLayer.L3,
            "importance": 0.7,
            "is_pinned": False,
            "metadata": {"origin": "agent-inferred"},
            "via_relation": {
                "relation_type": "depends_on",
                "relation_id": "e1",
                "anchor_item_id": anchor.id,
            },
        }],
    )
    result = await svc.pack("u1", "stripe webhooks")
    contents = [e["content"] for e in result["sections"]["memories"]]
    assert "webhook secret rotates monthly" in contents
    assert "↳ depends_on" in result["rendered"]


async def test_pack_token_budget_respected() -> None:
    svc = _service(memories=[_scored("m" * 4000) for _ in range(20)])
    result = await svc.pack("u1", "anything", max_tokens=500)
    assert result["token_estimate"] <= 500


async def test_pack_skill_body_promoted_when_budget_allows() -> None:
    svc = _service(
        skills=[{"name": "s1", "description": "short", "content": "the full body", "score": 0.9}],
    )
    result = await svc.pack("u1", "task", max_tokens=3000)
    top = result["sections"]["skills"][0]
    assert top.get("body_included") is True
    assert "_body" not in top


async def test_pack_skill_body_omitted_when_budget_tight() -> None:
    svc = _service(
        memories=[_scored("m" * 12000, score=0.99)],
        skills=[{"name": "s1", "description": "short", "content": "x" * 12000, "score": 0.9}],
    )
    result = await svc.pack("u1", "task", max_tokens=300)
    skills = result["sections"]["skills"]
    if skills:  # may be trimmed entirely under a tiny budget
        assert not skills[0].get("body_included")
        assert "_body" not in skills[0]


async def test_pack_no_index_no_code_section() -> None:
    svc = _service(memories=[_scored("fact")])
    result = await svc.pack("u1", "q")
    assert result["sections"]["code"] == []
    assert "Code (from index)" not in result["rendered"]
    assert "code" not in result["budget"]


async def test_pack_code_section_rendered() -> None:
    svc = _service(
        memories=[_scored("fact")],
        index_hits=[{
            "path": "src/core/budget.py",
            "symbol": "SectionBudgeter",
            "kind": "class",
            "language": "py",
            "line_start": 56,
            "line_end": 80,
            "content": "class SectionBudgeter:",
            "score": 0.9,
            "match": {"dense": True, "exact_symbol": True, "keyword": False},
        }],
    )
    result = await svc.pack("u1", "budgeting")
    assert result["sections"]["code"][0]["symbol"] == "SectionBudgeter"
    assert "src/core/budget.py:56" in result["rendered"]
    assert "```py" in result["rendered"]


async def test_pack_postgres_failure_degrades() -> None:
    svc = _service(memories=[_scored("fact")], postgres_fail=True)
    result = await svc.pack("u1", "q")
    assert result["sections"]["open_tasks"] == []
    assert "open_tasks section unavailable" in result.get("errors", [])


async def test_pack_empty_returns_diagnostics() -> None:
    svc = _service()
    result = await svc.pack("u1", "anything")
    assert "(no stored context matched this query)" in result["rendered"]
    assert result["diagnostics"]["total_items_for_user"] == 0


# ── render_pack ──────────────────────────────────────────────────────────────

def test_render_pack_stale_marker() -> None:
    rendered = render_pack(
        "q",
        {"memories": [{
            "content": "old fact", "memory_layer": "L3",
            "possibly_stale": True, "stale_files": ["src/x.py"],
        }]},
        "proj", 10,
    )
    assert "[STALE: src/x.py]" in rendered
