from __future__ import annotations

import pytest

from src.core.handoff import HandoffService
from src.models.handoff import (
    OPEN_HANDOFF_STATUSES,
    Handoff,
    HandoffReport,
    HandoffStatus,
    HandoffVersion,
)


class FakeHandoffStorage:
    """In-memory stand-in for the handoff methods of PostgresStorage."""

    def __init__(self) -> None:
        self.handoffs: dict[str, Handoff] = {}
        self.versions: dict[tuple[str, int], HandoffVersion] = {}
        self.reports: list[HandoffReport] = []

    async def handoff_create(self, h: Handoff) -> Handoff:
        if any(
            x.user_id == h.user_id and x.project == h.project and x.slug == h.slug
            for x in self.handoffs.values()
        ):
            raise ValueError("slug clash")
        self.handoffs[h.id] = h.model_copy(deep=True)
        self.versions[(h.id, h.version)] = HandoffVersion(
            handoff_id=h.id, version=h.version, body=h.body, author=h.created_by
        )
        return h

    async def handoff_get(self, user_id, handoff_id=None, *, project=None, slug=None):
        for h in self.handoffs.values():
            if h.user_id != user_id:
                continue
            if handoff_id is not None and h.id == handoff_id:
                return h.model_copy(deep=True)
            if handoff_id is None and h.project == project and h.slug == slug:
                return h.model_copy(deep=True)
        return None

    async def handoff_get_version(self, handoff_id, version):
        return self.versions.get((handoff_id, version))

    async def handoff_list(
        self, user_id, *, project=None, assignee=None, statuses=None, limit=50
    ):
        out = [h for h in self.handoffs.values() if h.user_id == user_id]
        if project is not None:
            out = [h for h in out if h.project == project]
        if assignee is not None:
            out = [h for h in out if h.assignee == assignee]
        if statuses:
            out = [h for h in out if h.status in statuses]
        out.sort(key=lambda h: h.updated_at, reverse=True)
        return [h.model_copy(deep=True) for h in out[:limit]]

    async def handoff_save(self, h, new_version=None):
        self.handoffs[h.id] = h.model_copy(deep=True)
        if new_version:
            self.versions[(new_version.handoff_id, new_version.version)] = new_version
        return h

    async def handoff_add_report(self, r, new_status=None):
        self.reports.append(r)
        if new_status is not None:
            self.handoffs[r.handoff_id].status = new_status
        return r

    async def handoff_reports(self, handoff_id, limit=10):
        rs = [r for r in self.reports if r.handoff_id == handoff_id]
        rs.sort(key=lambda r: r.created_at, reverse=True)
        return rs[:limit]


@pytest.fixture
def storage() -> FakeHandoffStorage:
    return FakeHandoffStorage()


@pytest.fixture
def svc(storage: FakeHandoffStorage) -> HandoffService:
    return HandoffService(storage)  # type: ignore[arg-type]


async def test_create_defaults_to_draft_with_slugified_title(svc):
    h = await svc.create("u1", "synatyx", "Add Handoff Tools!", "body v1")
    assert h.slug == "add-handoff-tools"
    assert h.status == HandoffStatus.DRAFT
    assert h.version == 1
    assert h.assignee is None


async def test_create_with_assignee_is_assigned(svc):
    h = await svc.create("u1", "synatyx", "Task", "b", assignee="synatyx-w1")
    assert h.status == HandoffStatus.ASSIGNED


async def test_create_explicit_status_and_slug_win(svc):
    h = await svc.create(
        "u1", "p", "T", "b", assignee="a", slug="My Slug", status=HandoffStatus.BLOCKED
    )
    assert h.status == HandoffStatus.BLOCKED
    assert h.slug == "my-slug"


async def test_slug_clash_raises_but_other_project_or_user_is_fine(svc):
    await svc.create("u1", "p", "Same", "b")
    with pytest.raises(ValueError):
        await svc.create("u1", "p", "Same", "b2")
    await svc.create("u1", "other", "Same", "b")
    await svc.create("u2", "p", "Same", "b")


async def test_create_parent_must_exist_for_user(svc):
    parent = await svc.create("u1", "p", "Parent", "b")
    child = await svc.create("u1", "p", "Child", "b", parent_id=parent.id)
    assert child.parent_id == parent.id
    with pytest.raises(ValueError):
        await svc.create("u1", "p", "Orphan", "b", parent_id="nope")
    with pytest.raises(ValueError):
        await svc.create("u2", "p", "Thief", "b", parent_id=parent.id)


async def test_get_by_id_and_by_project_slug(svc):
    h = await svc.create("u1", "p", "Thing", "b")
    by_id = await svc.get("u1", h.id)
    by_slug = await svc.get("u1", project="p", slug="thing")
    assert by_id is not None and by_slug is not None
    assert by_id[0].id == by_slug[0].id == h.id
    assert by_id[1] == []
    assert await svc.get("u1", "missing") is None
    assert await svc.get("u1", project="p", slug="missing") is None


async def test_get_with_version_returns_that_body(svc):
    h = await svc.create("u1", "p", "Thing", "v1 body")
    await svc.update("u1", h.id, body="v2 body")
    got = await svc.get("u1", h.id, version=1)
    assert got is not None
    assert got[0].body == "v1 body"
    assert got[0].version == 1
    current = await svc.get("u1", h.id)
    assert current is not None and current[0].body == "v2 body"
    with pytest.raises(ValueError):
        await svc.get("u1", h.id, version=9)


async def test_update_body_bumps_version_and_keeps_old_body(svc, storage):
    h = await svc.create("u1", "p", "Thing", "old")
    out = await svc.update("u1", h.id, body="new", author="kai")
    assert out is not None
    assert out.version == 2 and out.body == "new"
    assert storage.versions[(h.id, 1)].body == "old"
    assert storage.versions[(h.id, 2)].body == "new"
    assert storage.versions[(h.id, 2)].author == "kai"


async def test_update_without_body_or_same_body_does_not_bump(svc, storage):
    h = await svc.create("u1", "p", "Thing", "same")
    out = await svc.update("u1", h.id, title="Renamed", status=HandoffStatus.ASSIGNED)
    assert out is not None
    assert out.version == 1 and out.title == "Renamed"
    assert out.status == HandoffStatus.ASSIGNED
    same = await svc.update("u1", h.id, body="same", assignee="w1")
    assert same is not None and same.version == 1 and same.assignee == "w1"
    assert list(storage.versions) == [(h.id, 1)]


async def test_update_unknown_or_foreign_returns_none(svc):
    h = await svc.create("u1", "p", "Thing", "b")
    assert await svc.update("u1", "missing", body="x") is None
    assert await svc.update("u2", h.id, body="x") is None


async def test_report_without_status_keeps_status(svc):
    h = await svc.create("u1", "p", "Thing", "b", assignee="w1")
    r = await svc.report("u1", h.id, "w1", "started reading")
    assert r is not None and r.status_after is None
    got = await svc.get("u1", h.id)
    assert got is not None
    assert got[0].status == HandoffStatus.ASSIGNED
    assert [x.text for x in got[1]] == ["started reading"]


async def test_report_with_status_moves_handoff(svc):
    h = await svc.create("u1", "p", "Thing", "b", assignee="w1")
    r = await svc.report("u1", h.id, "w1", "PR up", status=HandoffStatus.DONE)
    assert r is not None and r.status_after == HandoffStatus.DONE
    got = await svc.get("u1", h.id)
    assert got is not None and got[0].status == HandoffStatus.DONE


async def test_report_unknown_or_foreign_returns_none(svc, storage):
    h = await svc.create("u1", "p", "Thing", "b")
    assert await svc.report("u1", "missing", "a", "t") is None
    assert await svc.report("u2", h.id, "a", "t") is None
    assert storage.reports == []


async def test_mine_returns_only_open_statuses(svc):
    ids = {}
    for status in HandoffStatus:
        h = await svc.create("u1", "p", f"H {status.value}", "b", assignee="w1", status=status)
        ids[status] = h.id
    await svc.create("u1", "p", "Someone else", "b", assignee="w2")
    mine = await svc.mine("u1", "w1")
    assert {h.id for h in mine} == {ids[s] for s in OPEN_HANDOFF_STATUSES}
    assert await svc.mine("u1", "w1", project="nope") == []


async def test_wrong_user_gets_nothing(svc):
    h = await svc.create("u1", "p", "Thing", "b", assignee="w1")
    assert await svc.get("u2", h.id) is None
    assert await svc.get("u2", project="p", slug="thing") is None
    assert await svc.list("u2") == []
    assert await svc.mine("u2", "w1") == []


async def test_list_filters_by_status(svc):
    await svc.create("u1", "p", "A", "b")
    await svc.create("u1", "p", "B", "b", assignee="w1")
    assert len(await svc.list("u1", project="p")) == 2
    only = await svc.list("u1", status=HandoffStatus.ASSIGNED)
    assert [h.title for h in only] == ["B"]
