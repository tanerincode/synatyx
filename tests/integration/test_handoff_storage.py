from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.models.handoff import (
    OPEN_HANDOFF_STATUSES,
    Handoff,
    HandoffReport,
    HandoffStatus,
    HandoffVersion,
)
from src.models.task import Task, TaskStatus
from src.storage.postgres import PostgresStorage


def _h(slug: str = "thing", user: str = "u1", project: str = "p", **kw: object) -> Handoff:
    fields: dict[str, object] = {
        "user_id": user, "project": project, "slug": slug, "title": slug.title(), "body": "v1",
    }
    fields.update(kw)
    return Handoff(**fields)  # type: ignore[arg-type]


async def test_create_and_get_by_id_and_slug(storage: PostgresStorage) -> None:
    h = await storage.handoff_create(_h(assignee="w1", status=HandoffStatus.ASSIGNED))

    by_id = await storage.handoff_get("u1", h.id)
    by_slug = await storage.handoff_get("u1", project="p", slug="thing")
    assert by_id is not None and by_slug is not None
    assert by_id.id == by_slug.id == h.id
    assert by_id.status == HandoffStatus.ASSIGNED and by_id.assignee == "w1"

    v1 = await storage.handoff_get_version(h.id, 1)
    assert v1 is not None and v1.body == "v1"
    assert await storage.handoff_get("u1", "missing") is None
    assert await storage.handoff_get("u2", h.id) is None  # scoped to the user


async def test_slug_clash_and_allowed_variants(storage: PostgresStorage) -> None:
    await storage.handoff_create(_h())
    with pytest.raises(ValueError):
        await storage.handoff_create(_h())
    await storage.handoff_create(_h(user="u2"))
    await storage.handoff_create(_h(project="other"))


async def test_save_with_new_version(storage: PostgresStorage) -> None:
    h = await storage.handoff_create(_h())
    h.version = 2
    h.body = "v2"
    h.title = "Renamed"
    h.touch()
    v2_row = HandoffVersion(handoff_id=h.id, version=2, body="v2", author="kai")
    await storage.handoff_save(h, v2_row)

    got = await storage.handoff_get("u1", h.id)
    assert got is not None and (got.version, got.body, got.title) == (2, "v2", "Renamed")
    v1 = await storage.handoff_get_version(h.id, 1)
    v2 = await storage.handoff_get_version(h.id, 2)
    assert v1 is not None and v1.body == "v1"
    assert v2 is not None and v2.body == "v2" and v2.author == "kai"
    assert await storage.handoff_get_version(h.id, 3) is None


async def test_add_report_moves_status_and_reports_newest_first(
    storage: PostgresStorage,
) -> None:
    h = await storage.handoff_create(_h(assignee="w1", status=HandoffStatus.ASSIGNED))
    now = datetime.now(UTC)
    await storage.handoff_add_report(
        HandoffReport(handoff_id=h.id, agent="w1", text="first", created_at=now)
    )
    unchanged = await storage.handoff_get("u1", h.id)
    assert unchanged is not None and unchanged.status == HandoffStatus.ASSIGNED

    await storage.handoff_add_report(
        HandoffReport(
            handoff_id=h.id, agent="w1", text="second",
            status_after=HandoffStatus.BLOCKED, created_at=now + timedelta(seconds=5),
        ),
        HandoffStatus.BLOCKED,
    )
    moved = await storage.handoff_get("u1", h.id)
    assert moved is not None and moved.status == HandoffStatus.BLOCKED

    reports = await storage.handoff_reports(h.id)
    assert [r.text for r in reports] == ["second", "first"]
    assert reports[0].status_after == HandoffStatus.BLOCKED
    assert reports[1].status_after is None
    assert len(await storage.handoff_reports(h.id, limit=1)) == 1


async def test_list_and_mine_filters(storage: PostgresStorage) -> None:
    await storage.handoff_create(_h("a", assignee="w1", status=HandoffStatus.ASSIGNED))
    await storage.handoff_create(_h("b", assignee="w1", status=HandoffStatus.DONE))
    await storage.handoff_create(_h("c", assignee="w2", status=HandoffStatus.IN_PROGRESS))
    await storage.handoff_create(_h("d", project="other"))
    await storage.handoff_create(_h("e", user="u2", assignee="w1", status=HandoffStatus.ASSIGNED))

    async def slugs(**kw: object) -> list[str]:
        return sorted(h.slug for h in await storage.handoff_list("u1", **kw))  # type: ignore[arg-type]

    assert await slugs() == ["a", "b", "c", "d"]
    assert await slugs(project="p") == ["a", "b", "c"]
    assert await slugs(assignee="w1") == ["a", "b"]
    assert await slugs(statuses=[HandoffStatus.DONE]) == ["b"]
    mine = await slugs(assignee="w1", statuses=list(OPEN_HANDOFF_STATUSES))
    assert mine == ["a"]
    assert len(await storage.handoff_list("u1", limit=2)) == 2


async def test_list_orders_by_updated_desc(storage: PostgresStorage) -> None:
    old = _h("old")
    old.updated_at = datetime.now(UTC) - timedelta(days=1)
    await storage.handoff_create(old)
    await storage.handoff_create(_h("new"))
    assert [h.slug for h in await storage.handoff_list("u1")] == ["new", "old"]


async def test_task_assignee_add_list_update(storage: PostgresStorage) -> None:
    t1 = await storage.task_add(Task(user_id="u1", title="one", assignee="w1"))
    await storage.task_add(Task(user_id="u1", title="two", assignee="w2"))
    await storage.task_add(Task(user_id="u1", title="three"))
    assert t1.assignee == "w1"

    assert [t.title for t in await storage.task_list("u1", assignee="w1")] == ["one"]
    assert len(await storage.task_list("u1")) == 3

    updated = await storage.task_update(t1.id, "u1", assignee="w2", status=TaskStatus.IN_PROGRESS)
    assert updated is not None and updated.assignee == "w2"
    assert {t.title for t in await storage.task_list("u1", assignee="w2")} == {"one", "two"}
    assert await storage.task_update(t1.id, "u2", assignee="w9") is None
