from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.handoff import HandoffService
from src.models.handoff import Handoff, HandoffReport, HandoffStatus, HandoffVersion


class FakeHandoffStorage:
    """In-memory stand-in for the handoff half of PostgresStorage."""

    def __init__(self) -> None:
        self.handoffs: dict[str, Handoff] = {}
        self.versions: list[HandoffVersion] = []
        self.reports: list[HandoffReport] = []

    async def handoff_create(self, h: Handoff) -> Handoff:
        self.handoffs[h.id] = h
        self.versions.append(HandoffVersion(handoff_id=h.id, version=1, body=h.body))
        return h

    async def handoff_get(
        self,
        user_id: str,
        handoff_id: str | None = None,
        *,
        project: str | None = None,
        slug: str | None = None,
    ) -> Handoff | None:
        for h in self.handoffs.values():
            if h.user_id != user_id:
                continue
            if handoff_id is not None and h.id == handoff_id:
                return h
            if handoff_id is None and h.project == project and h.slug == slug:
                return h
        return None

    async def handoff_get_version(self, handoff_id: str, version: int) -> HandoffVersion | None:
        for v in self.versions:
            if v.handoff_id == handoff_id and v.version == version:
                return v
        return None

    def _filter(
        self,
        user_id: str | None,
        project: str | None,
        assignee: str | None,
        statuses: list[HandoffStatus] | None,
        limit: int,
    ) -> list[Handoff]:
        out = [
            h for h in self.handoffs.values()
            if (user_id is None or h.user_id == user_id)
            and (project is None or h.project == project)
            and (assignee is None or h.assignee == assignee)
            and (not statuses or h.status in statuses)
        ]
        out.sort(key=lambda h: h.updated_at, reverse=True)
        return out[:limit]

    async def handoff_list(
        self,
        user_id: str,
        *,
        project: str | None = None,
        assignee: str | None = None,
        statuses: list[HandoffStatus] | None = None,
        limit: int = 50,
    ) -> list[Handoff]:
        return self._filter(user_id, project, assignee, statuses, limit)

    async def handoff_list_all(
        self,
        *,
        project: str | None = None,
        assignee: str | None = None,
        statuses: list[HandoffStatus] | None = None,
        limit: int = 50,
    ) -> list[Handoff]:
        return self._filter(None, project, assignee, statuses, limit)

    async def handoff_save(
        self, h: Handoff, new_version: HandoffVersion | None = None
    ) -> Handoff:
        self.handoffs[h.id] = h
        if new_version is not None:
            self.versions.append(new_version)
        return h

    async def handoff_add_report(
        self, r: HandoffReport, new_status: HandoffStatus | None = None
    ) -> HandoffReport:
        self.reports.append(r)
        if new_status is not None:
            self.handoffs[r.handoff_id].status = new_status
        return r

    async def handoff_reports(self, handoff_id: str, limit: int = 10) -> list[HandoffReport]:
        mine = [r for r in self.reports if r.handoff_id == handoff_id]
        mine.sort(key=lambda r: r.created_at, reverse=True)
        return mine[:limit]

    async def handoff_versions(self, handoff_id: str) -> list[dict[str, Any]]:
        mine = [v for v in self.versions if v.handoff_id == handoff_id]
        mine.sort(key=lambda v: v.version, reverse=True)
        return [
            {"version": v.version, "author": v.author, "created_at": v.created_at}
            for v in mine
        ]

    async def handoff_last_reports(self, ids: list[str]) -> dict[str, HandoffReport]:
        out: dict[str, HandoffReport] = {}
        for r in sorted(self.reports, key=lambda r: r.created_at):
            if r.handoff_id in ids:
                out[r.handoff_id] = r
        return out


def _service() -> tuple[HandoffService, FakeHandoffStorage]:
    storage = FakeHandoffStorage()
    return HandoffService(storage), storage  # type: ignore[arg-type]


async def test_list_all_spans_users_and_filters() -> None:
    svc, _ = _service()
    await svc.create("u1", "p1", "One", "b", assignee="w1")
    await svc.create("u2", "p1", "Two", "b")
    await svc.create("u2", "p2", "Three", "b", assignee="w1")

    assert len(await svc.list_all()) == 3
    assert {h.title for h in await svc.list_all(project="p1")} == {"One", "Two"}
    assert {h.title for h in await svc.list_all(assignee="w1")} == {"One", "Three"}
    drafts = await svc.list_all(status=HandoffStatus.DRAFT)
    assert [h.title for h in drafts] == ["Two"]


async def test_versions_newest_first_without_bodies() -> None:
    svc, _ = _service()
    h = await svc.create("u1", "p1", "One", "v1 body")
    await svc.update("u1", h.id, body="v2 body", author="kai")

    versions = await svc.versions(h.id)
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[0]["author"] == "kai"
    assert all("body" not in v for v in versions)


async def test_last_reports_returns_newest_per_handoff() -> None:
    svc, storage = _service()
    a = await svc.create("u1", "p1", "A", "b")
    b = await svc.create("u1", "p1", "B", "b")
    await svc.report("u1", a.id, "w1", "first")
    await svc.report("u1", a.id, "w1", "second")
    storage.reports[1].created_at = datetime.now(UTC) + timedelta(minutes=1)

    last = await svc.last_reports([a.id, b.id])
    assert last[a.id].text == "second"
    assert b.id not in last
    assert await svc.last_reports([]) == {}


async def test_get_report_limit() -> None:
    svc, _ = _service()
    h = await svc.create("u1", "p1", "A", "b")
    for i in range(15):
        await svc.report("u1", h.id, "w1", f"r{i}")
    found = await svc.get("u1", h.id)
    assert found is not None and len(found[1]) == 10
    found = await svc.get("u1", h.id, report_limit=100)
    assert found is not None and len(found[1]) == 15
