from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.brief import BriefService
from src.core.retrieve import empty_retrieve_diagnostics
from src.models.context import ContextItem
from src.models.handoff import OPEN_HANDOFF_STATUSES, Handoff, HandoffStatus
from src.models.memory_layer import MemoryLayer
from src.models.task import Task, TaskStatus


class FakeQdrantBrief:
    def __init__(self, items: list[ContextItem]) -> None:
        self._items = items

    async def list_items(
        self,
        user_id: str,
        memory_layer: MemoryLayer | None = None,
        project: str | None = None,
        limit: int = 50,
        **kwargs: Any,
    ) -> list[ContextItem]:
        out = []
        for item in self._items:
            if item.user_id != user_id or item.is_deprecated:
                continue
            if memory_layer and item.memory_layer != memory_layer:
                continue
            if project and item.metadata.get("project") != project:
                continue
            out.append(item)
        return out[:limit]

    async def count_items(
        self,
        user_id: str,
        memory_layer: MemoryLayer | None = None,
        project: str | None = None,
        **kwargs: Any,
    ) -> int:
        return len(await self.list_items(user_id, memory_layer, project, limit=10_000))


class FakePostgresBrief:
    def __init__(
        self, tasks: list[Task] | None = None, handoffs: list[Handoff] | None = None
    ) -> None:
        self.tasks = tasks or []
        self.handoffs = handoffs or []
        self.handoff_fail = False
        self.handoff_calls: list[dict[str, Any]] = []

    async def handoff_list(
        self,
        user_id: str,
        *,
        project: str | None = None,
        assignee: str | None = None,
        statuses: list[HandoffStatus] | None = None,
        limit: int = 50,
    ) -> list[Handoff]:
        self.handoff_calls.append(
            {"project": project, "assignee": assignee, "statuses": statuses}
        )
        if self.handoff_fail:
            raise RuntimeError("db down")
        out = [h for h in self.handoffs if h.user_id == user_id]
        if project:
            out = [h for h in out if h.project == project]
        if assignee:
            out = [h for h in out if h.assignee == assignee]
        if statuses:
            out = [h for h in out if h.status in statuses]
        return out[:limit]

    async def task_list(
        self,
        user_id: str,
        status: TaskStatus | None = None,
        priority: Any = None,
        project: str | None = None,
        limit: int = 50,
    ) -> list[Task]:
        out = [t for t in self.tasks if t.user_id == user_id]
        if status:
            out = [t for t in out if t.status == status]
        if project:
            out = [t for t in out if t.project == project]
        return out[:limit]


def _item(
    content: str,
    layer: MemoryLayer = MemoryLayer.L3,
    user_id: str = "u1",
    days_old: float = 0.0,
    **overrides: Any,
) -> ContextItem:
    base: dict[str, Any] = {
        "user_id": user_id,
        "content": content,
        "memory_layer": layer,
        "created_at": datetime.now(UTC) - timedelta(days=days_old),
    }
    base.update(overrides)
    return ContextItem(**base)


def _service(
    project_items: list[ContextItem],
    l4_items: list[ContextItem] | None = None,
    tasks: list[Task] | None = None,
    handoffs: list[Handoff] | None = None,
) -> BriefService:
    return BriefService(
        FakeQdrantBrief(project_items),  # type: ignore[arg-type]
        FakeQdrantBrief(l4_items or []),  # type: ignore[arg-type]
        FakePostgresBrief(tasks, handoffs),  # type: ignore[arg-type]
    )


# ── BriefService ─────────────────────────────────────────────────────────────

async def test_brief_sections_populated() -> None:
    svc = _service(
        project_items=[
            _item("last session summary", MemoryLayer.L2, days_old=1),
            _item("uses Qdrant on port 6333", MemoryLayer.L3, days_old=30),
        ],
        l4_items=[_item("prefers clean commits", MemoryLayer.L4)],
        tasks=[Task(user_id="u1", title="finish GC docs")],
    )
    brief = await svc.brief("u1")

    assert [i["content"] for i in brief["identity"]] == ["prefers clean commits"]
    assert [i["content"] for i in brief["last_session"]] == ["last session summary"]
    assert [i["content"] for i in brief["project_knowledge"]] == ["uses Qdrant on port 6333"]
    assert [t["title"] for t in brief["open_tasks"]] == ["finish GC docs"]
    assert brief["stats"]["items_by_layer"] == {"L2": 1, "L3": 1, "L4": 1}
    assert brief["stats"]["total_items"] == 3
    assert brief["token_estimate"] > 0
    assert brief["max_tokens"] == 2000


async def test_brief_pinned_checkpoints_come_first() -> None:
    svc = _service(project_items=[
        _item("high importance fact", MemoryLayer.L3, importance=0.9),
        _item("[Checkpoint: v1] pinned decision", MemoryLayer.L3, importance=1.0, is_pinned=True),
    ])
    brief = await svc.brief("u1")
    assert brief["project_knowledge"][0]["is_pinned"] is True


async def test_brief_recent_changes_exclude_already_shown() -> None:
    svc = _service(project_items=[
        _item("fresh fact", MemoryLayer.L3, days_old=1),
        _item("old fact", MemoryLayer.L3, days_old=60, importance=0.9),
    ])
    brief = await svc.brief("u1")
    knowledge_ids = {i["id"] for i in brief["project_knowledge"]}
    # both fit in knowledge, so recent_changes must not repeat the fresh one
    assert all(i["id"] not in knowledge_ids for i in brief["recent_changes"])


async def test_brief_attempts_get_own_section() -> None:
    svc = _service(project_items=[
        _item(
            "tried sync qdrant client, failed: event loop conflict",
            MemoryLayer.L2,
            metadata={"type": "attempt", "outcome": "failed"},
        ),
        _item("normal episodic note", MemoryLayer.L2),
    ])
    brief = await svc.brief("u1")
    assert len(brief["recent_attempts"]) == 1
    assert "event loop" in brief["recent_attempts"][0]["content"]
    # attempts must not leak into last_session
    assert [i["content"] for i in brief["last_session"]] == ["normal episodic note"]


async def test_brief_excludes_skill_embeddings() -> None:
    svc = _service(project_items=[
        _item("nodejs-developer", MemoryLayer.L3, metadata={"type": "skill"}),
        _item("real project fact", MemoryLayer.L3),
    ])
    brief = await svc.brief("u1")
    assert [i["content"] for i in brief["project_knowledge"]] == ["real project fact"]


async def test_brief_token_budget_trims() -> None:
    items = [
        _item(f"fact {i}: " + "x" * 400, MemoryLayer.L3, importance=0.5)
        for i in range(50)
    ]
    svc = _service(project_items=items)
    brief = await svc.brief("u1", max_tokens=1000)
    # knowledge gets 35% of 1000 = 350 tokens; each item ~102 tokens → ~3 items
    assert 0 < len(brief["project_knowledge"]) < 10
    assert brief["token_estimate"] <= 1000


async def test_brief_oversized_first_item_is_truncated_not_dropped() -> None:
    svc = _service(project_items=[
        _item("[Checkpoint: big] " + "y" * 8000, MemoryLayer.L3, is_pinned=True, importance=1.0),
    ])
    brief = await svc.brief("u1", max_tokens=400)
    assert len(brief["project_knowledge"]) == 1
    assert brief["project_knowledge"][0]["truncated"] is True
    assert len(brief["project_knowledge"][0]["content"]) < 8000


async def test_brief_empty_store() -> None:
    svc = _service(project_items=[])
    brief = await svc.brief("u1")
    assert brief["identity"] == []
    assert brief["project_knowledge"] == []
    assert brief["open_tasks"] == []
    assert brief["stats"]["total_items"] == 0


async def test_brief_carries_origin() -> None:
    svc = _service(project_items=[
        _item("user said they deploy on Fridays", MemoryLayer.L3,
              metadata={"origin": "user-stated"}),
    ])
    brief = await svc.brief("u1")
    assert brief["project_knowledge"][0]["origin"] == "user-stated"


async def test_brief_in_progress_tasks_before_pending() -> None:
    svc = _service(
        project_items=[],
        tasks=[
            Task(user_id="u1", title="pending thing"),
            Task(user_id="u1", title="active thing", status=TaskStatus.IN_PROGRESS),
        ],
    )
    brief = await svc.brief("u1")
    assert [t["title"] for t in brief["open_tasks"]] == ["active thing", "pending thing"]


# ── empty_retrieve_diagnostics ───────────────────────────────────────────────

def test_diagnostics_nothing_stored() -> None:
    diag = empty_retrieve_diagnostics(0, {"L2": 0, "L3": 0, "L4": 0}, [MemoryLayer.L3])
    assert diag["matched"] == 0
    assert diag["total_items_for_user"] == 0
    assert "no memories" in diag["hint"].lower()


def test_diagnostics_filters_missed() -> None:
    diag = empty_retrieve_diagnostics(
        12, {"L2": 2, "L3": 9, "L4": 1}, [MemoryLayer.L3],
        session_id="other-project", project="other-project",
    )
    assert diag["filters_applied"] == ["session_id", "project"]
    assert "12 memories exist" in diag["hint"]
    assert "filter" in diag["hint"]


def test_diagnostics_layers_missed() -> None:
    diag = empty_retrieve_diagnostics(5, {"L2": 5, "L3": 0, "L4": 0}, [MemoryLayer.L3])
    assert diag["filters_applied"] == []
    assert "L3" in diag["hint"]
    assert diag["items_by_layer"]["L2"] == 5


async def test_brief_open_tasks_scoped_to_project() -> None:
    svc = _service(
        project_items=[],
        tasks=[
            Task(user_id="u1", title="this project", project="p1"),
            Task(user_id="u1", title="other project", project="p2"),
            Task(user_id="u1", title="no project"),
        ],
    )
    brief = await svc.brief("u1", project="p1")
    titles = [t["title"] for t in brief["open_tasks"]]
    assert titles == ["this project"]


async def test_brief_without_project_keeps_legacy_unfiltered_tasks() -> None:
    # dispatch always resolves a project when one is determinable; a truly
    # unscoped brief (no project anywhere) still returns everything
    svc = _service(
        project_items=[],
        tasks=[
            Task(user_id="u1", title="a", project="p1"),
            Task(user_id="u1", title="b", project="p2"),
        ],
    )
    brief = await svc.brief("u1")
    assert len(brief["open_tasks"]) == 2


# ── open_handoffs ────────────────────────────────────────────────────────────


def _handoff(
    slug: str,
    assignee: str | None = "w3",
    status: HandoffStatus = HandoffStatus.ASSIGNED,
    project: str = "synatyx",
    user_id: str = "u1",
) -> Handoff:
    return Handoff(
        user_id=user_id,
        project=project,
        slug=slug,
        title=f"Title {slug}",
        assignee=assignee,
        status=status,
        body="SECRET BODY " * 50,
    )


async def test_brief_open_handoffs_listed_without_body() -> None:
    h = _handoff("pr-c", status=HandoffStatus.IN_PROGRESS)
    svc = _service([], handoffs=[h])
    result = await svc.brief("u1", project="synatyx", assignee="w3")
    assert result["open_handoffs"] == [
        {
            "id": h.id,
            "slug": "pr-c",
            "title": "Title pr-c",
            "status": "in_progress",
            "version": 1,
            "updated_at": h.updated_at.isoformat(),
        }
    ]
    assert "SECRET BODY" not in str(result)
    calls = svc._postgres.handoff_calls  # type: ignore[attr-defined]
    assert calls == [
        {"project": "synatyx", "assignee": "w3", "statuses": list(OPEN_HANDOFF_STATUSES)}
    ]


async def test_brief_without_assignee_has_no_handoff_section() -> None:
    svc = _service([], handoffs=[_handoff("pr-c")])
    result = await svc.brief("u1", project="synatyx")
    assert "open_handoffs" not in result
    assert svc._postgres.handoff_calls == []  # type: ignore[attr-defined]


async def test_brief_open_handoffs_excludes_other_assignees_and_closed() -> None:
    mine = _handoff("mine", status=HandoffStatus.BLOCKED)
    others = [
        _handoff("theirs", assignee="w1"),
        _handoff("done", status=HandoffStatus.DONE),
        _handoff("cancelled", status=HandoffStatus.CANCELLED),
        _handoff("draft", status=HandoffStatus.DRAFT),
    ]
    svc = _service([], handoffs=[mine, *others])
    result = await svc.brief("u1", project="synatyx", assignee="w3")
    assert [h["slug"] for h in result["open_handoffs"]] == ["mine"]


async def test_brief_open_handoffs_storage_failure_is_swallowed() -> None:
    svc = _service([_item("fact", metadata={"project": "synatyx"})], handoffs=[_handoff("pr-c")])
    svc._postgres.handoff_fail = True  # type: ignore[attr-defined]
    result = await svc.brief("u1", project="synatyx", assignee="w3")
    assert result["open_handoffs"] == []
    assert len(result["project_knowledge"]) == 1


async def test_section_budget_sums_to_one() -> None:
    from src.core.brief import SECTION_BUDGET

    assert abs(sum(SECTION_BUDGET.values()) - 1.0) < 1e-9


async def test_brief_open_handoffs_budget_truncates_but_keeps_one() -> None:
    handoffs = [_handoff(f"slug-{i}") for i in range(40)]
    svc = _service([], handoffs=handoffs)
    full = await svc.brief("u1", project="synatyx", assignee="w3", max_tokens=100_000)
    small = await svc.brief("u1", project="synatyx", assignee="w3", max_tokens=100)
    assert len(full["open_handoffs"]) == 40
    assert 1 <= len(small["open_handoffs"]) < 40
