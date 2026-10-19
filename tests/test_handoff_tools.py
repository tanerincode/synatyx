from __future__ import annotations

import types
from datetime import UTC, datetime
from typing import Any

import pytest

import src.transports.mcp.server as server_mod
from src.models.handoff import (
    OPEN_HANDOFF_STATUSES,
    Handoff,
    HandoffReport,
    HandoffStatus,
)
from src.models.task import Task
from src.transports.mcp.server import SynatyxMCPServer

USER = "tester"


class FakeHandoffService:
    """In-memory stand-in honouring the HandoffService contract.

    The ValueError wording is copied from src/core/handoff.py, because the
    server classifies those messages into error codes.
    """

    def __init__(self, storage: Any = None) -> None:
        self.items: dict[str, Handoff] = {}
        self.bodies: dict[tuple[str, int], str] = {}
        self.reports: list[HandoffReport] = []
        self.authors: dict[tuple[str, int], str | None] = {}

    async def create(self, user_id, project, title, body, *, assignee=None, slug=None,
                     parent_id=None, status=None, task_ids=None, created_by=None):
        slug = slug or title.lower().replace(" ", "-")
        if any(h.project == project and h.slug == slug and h.user_id == user_id
               for h in self.items.values()):
            raise ValueError(f"handoff slug '{slug}' already exists in project '{project}'")
        if parent_id and parent_id not in self.items:
            raise ValueError(f"parent handoff '{parent_id}' not found")
        h = Handoff(
            user_id=user_id, project=project, slug=slug, title=title, body=body,
            assignee=assignee, parent_id=parent_id, task_ids=task_ids or [],
            created_by=created_by,
            status=status or (HandoffStatus.ASSIGNED if assignee else HandoffStatus.DRAFT),
        )
        self.items[h.id] = h
        self.bodies[(h.id, 1)] = body
        return h

    def _find(self, user_id, handoff_id=None, project=None, slug=None):
        for h in self.items.values():
            if h.user_id != user_id:
                continue
            if handoff_id and h.id == handoff_id:
                return h
            if not handoff_id and h.project == project and h.slug == slug:
                return h
        return None

    async def get(self, user_id, handoff_id=None, *, project=None, slug=None, version=None):
        h = self._find(user_id, handoff_id, project, slug)
        if h is None:
            return None
        if version is not None:
            if (h.id, version) not in self.bodies:
                raise ValueError(f"version {version} of handoff '{h.id}' not found")
            h = h.model_copy(update={"version": version, "body": self.bodies[(h.id, version)]})
        reps = [r for r in self.reports if r.handoff_id == h.id][::-1]
        return h, reps[:10]

    async def list(self, user_id, *, project=None, assignee=None, status=None, limit=50):
        return [
            h for h in self.items.values()
            if h.user_id == user_id
            and (project is None or h.project == project)
            and (assignee is None or h.assignee == assignee)
            and (status is None or h.status == status)
        ]

    async def mine(self, user_id, assignee, *, project=None):
        return [h for h in await self.list(user_id, project=project, assignee=assignee)
                if h.status in OPEN_HANDOFF_STATUSES]

    async def update(self, user_id, handoff_id, *, body=None, title=None, status=None,
                     assignee=None, author=None):
        h = self._find(user_id, handoff_id)
        if h is None:
            return None
        if body is not None and body != h.body:
            h.version += 1
            h.body = body
            self.bodies[(h.id, h.version)] = body
            self.authors[(h.id, h.version)] = author
        if title is not None:
            h.title = title
        if status is not None:
            h.status = status
        if assignee is not None:
            h.assignee = assignee
        return h

    async def report(self, user_id, handoff_id, agent, text, *, status=None):
        h = self._find(user_id, handoff_id)
        if h is None:
            return None
        r = HandoffReport(handoff_id=h.id, agent=agent, text=text, status_after=status)
        self.reports.append(r)
        if status is not None:
            h.status = status
        return r


class FakeTaskStorage:
    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}

    async def task_add(self, task: Task) -> Task:
        self.tasks[task.id] = task
        return task

    async def task_list(self, user_id, status=None, priority=None, project=None,
                        assignee=None, limit=50):
        return [t for t in self.tasks.values()
                if assignee is None or t.assignee == assignee]

    async def task_update(self, task_id, user_id, status=None, priority=None, title=None,
                          description=None, assignee=None):
        t = self.tasks.get(task_id)
        if t is None:
            return None
        if assignee is not None:
            t.assignee = assignee
        t.updated_at = datetime.now(UTC)
        return t


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> SynatyxMCPServer:
    fake = FakeHandoffService()
    monkeypatch.setattr(server_mod, "HandoffService", lambda storage: fake)
    srv = SynatyxMCPServer.__new__(SynatyxMCPServer)
    srv.fake_handoffs = fake  # type: ignore[attr-defined]
    srv._postgres = FakeTaskStorage()  # type: ignore[assignment]

    async def _get_services(user_id: str, project: str | None = None):
        return types.SimpleNamespace(collection_name="ctx_x"), None, None, None, None

    srv._get_services = _get_services  # type: ignore[method-assign]
    return srv


async def _create(server: SynatyxMCPServer, **over: Any) -> dict[str, Any]:
    args = {"user_id": USER, "project": "Synatyx", "title": "Build tools",
            "body": "Do the thing", "assignee": "w2", **over}
    return await server._dispatch("context_handoff_create", args)


async def test_create_returns_metadata_without_body(server):
    res = await _create(server)
    h = res["handoff"]
    assert h["project"] == "synatyx"  # slugified
    assert h["slug"] == "build-tools"
    assert h["status"] == "assigned"
    assert h["version"] == 1
    assert "body" not in h


async def test_create_without_assignee_is_draft(server):
    res = await _create(server, assignee=None)
    assert res["handoff"]["status"] == "draft"


async def test_create_slug_clash_is_an_error_dict(server):
    await _create(server)
    res = await _create(server)
    assert "already exists" in res["error"]
    assert res["code"] == "CONFLICT"


async def test_create_bad_status_is_an_error_dict(server):
    res = await _create(server, status="nonsense")
    assert "Invalid status" in res["error"]
    assert res["code"] == "INVALID"


async def test_create_requires_project(server):
    res = await server._dispatch(
        "context_handoff_create", {"user_id": USER, "title": "t", "body": "b"}
    )
    assert "project" in res["error"]
    assert res["code"] == "INVALID"


async def test_get_by_id_returns_body_and_reports(server):
    hid = (await _create(server))["handoff"]["id"]
    await server._dispatch(
        "context_handoff_report", {"user_id": USER, "id": hid, "agent": "w2", "text": "halfway"}
    )
    res = await server._dispatch("context_handoff_get", {"user_id": USER, "id": hid})
    assert res["handoff"]["body"] == "Do the thing"
    assert [r["text"] for r in res["reports"]] == ["halfway"]


async def test_get_by_project_and_slug(server):
    await _create(server)
    res = await server._dispatch(
        "context_handoff_get", {"user_id": USER, "project": "Synatyx", "slug": "build-tools"}
    )
    assert res["handoff"]["title"] == "Build tools"
    assert res["handoff"]["body"] == "Do the thing"


async def test_get_keeps_only_last_ten_reports(server):
    hid = (await _create(server))["handoff"]["id"]
    for i in range(12):
        await server._dispatch(
            "context_handoff_report",
            {"user_id": USER, "id": hid, "agent": "w2", "text": f"r{i}"},
        )
    res = await server._dispatch("context_handoff_get", {"user_id": USER, "id": hid})
    assert len(res["reports"]) == 10
    assert res["reports"][0]["text"] == "r11"  # newest first


async def test_get_old_version(server):
    hid = (await _create(server))["handoff"]["id"]
    await server._dispatch("context_handoff_update", {"user_id": USER, "id": hid, "body": "v2"})
    res = await server._dispatch("context_handoff_get", {"user_id": USER, "id": hid, "version": 1})
    assert res["handoff"]["body"] == "Do the thing"
    assert res["handoff"]["version"] == 1


async def test_get_unknown_version_is_an_error_dict(server):
    hid = (await _create(server))["handoff"]["id"]
    res = await server._dispatch("context_handoff_get", {"user_id": USER, "id": hid, "version": 9})
    assert "not found" in res["error"]
    assert res["code"] == "NOT_FOUND"


async def test_get_not_found(server):
    res = await server._dispatch("context_handoff_get", {"user_id": USER, "id": "nope"})
    assert "not found" in res["error"]
    assert res["code"] == "NOT_FOUND"


async def test_get_needs_an_identifier(server):
    res = await server._dispatch("context_handoff_get", {"user_id": USER})
    assert "id" in res["error"]
    assert res["code"] == "INVALID"


async def test_list_filters_and_has_no_body(server):
    await _create(server)
    await _create(server, title="Other", assignee="w3")
    res = await server._dispatch("context_handoff_list", {"user_id": USER, "assignee": "w3"})
    assert res["count"] == 1
    assert res["handoffs"][0]["title"] == "Other"
    assert all("body" not in h for h in res["handoffs"])


async def test_list_by_status(server):
    await _create(server)
    await _create(server, title="Draft one", assignee=None)
    res = await server._dispatch("context_handoff_list", {"user_id": USER, "status": "draft"})
    assert [h["title"] for h in res["handoffs"]] == ["Draft one"]


async def test_mine_returns_only_open_without_body(server):
    open_id = (await _create(server))["handoff"]["id"]
    done_id = (await _create(server, title="Finished"))["handoff"]["id"]
    await server._dispatch(
        "context_handoff_update", {"user_id": USER, "id": done_id, "status": "done"}
    )
    res = await server._dispatch("context_handoff_mine", {"user_id": USER, "assignee": "w2"})
    assert [h["id"] for h in res["handoffs"]] == [open_id]
    assert all("body" not in h for h in res["handoffs"])


async def test_update_bumps_version_only_when_body_changes(server):
    hid = (await _create(server))["handoff"]["id"]
    same = await server._dispatch(
        "context_handoff_update", {"user_id": USER, "id": hid, "body": "Do the thing"}
    )
    assert same["handoff"]["version"] == 1
    changed = await server._dispatch(
        "context_handoff_update",
        {"user_id": USER, "id": hid, "body": "New body", "title": "Renamed", "assignee": "w3"},
    )
    assert changed["handoff"]["version"] == 2
    assert changed["handoff"]["title"] == "Renamed"
    assert changed["handoff"]["assignee"] == "w3"
    assert "body" not in changed["handoff"]


async def test_update_not_found(server):
    res = await server._dispatch(
        "context_handoff_update", {"user_id": USER, "id": "nope", "title": "x"}
    )
    assert "not found" in res["error"]
    assert res["code"] == "NOT_FOUND"


async def test_report_moves_the_status(server):
    hid = (await _create(server))["handoff"]["id"]
    res = await server._dispatch(
        "context_handoff_report",
        {"user_id": USER, "id": hid, "agent": "w2", "text": "all done", "status": "done"},
    )
    assert res["report"]["status_after"] == "done"
    assert res["report"]["text"] == "all done"
    assert res["handoff"]["status"] == "done"


async def test_report_not_found(server):
    res = await server._dispatch(
        "context_handoff_report", {"user_id": USER, "id": "nope", "agent": "w2", "text": "x"}
    )
    assert "not found" in res["error"]
    assert res["code"] == "NOT_FOUND"


async def test_report_missing_argument_is_an_error_dict(server):
    res = await server._dispatch("context_handoff_report", {"user_id": USER, "id": "x"})
    assert "agent" in res["error"]
    assert res["code"] == "INVALID"


async def test_task_assignee_round_trip(server):
    added = await server._dispatch(
        "context_task_add", {"user_id": USER, "title": "T", "assignee": "w2"}
    )
    assert added["assignee"] == "w2"
    await server._dispatch("context_task_add", {"user_id": USER, "title": "Other"})

    listed = await server._dispatch("context_task_list", {"user_id": USER, "assignee": "w2"})
    assert [t["title"] for t in listed["tasks"]] == ["T"]
    assert listed["tasks"][0]["assignee"] == "w2"

    updated = await server._dispatch(
        "context_task_update",
        {"user_id": USER, "task_id": added["task_id"], "assignee": "w3"},
    )
    assert updated["assignee"] == "w3"


async def test_create_records_the_session_name_as_author(server):
    res = await _create(server, created_by="synatyx-lead")
    assert res["handoff"]["created_by"] == "synatyx-lead"


async def test_create_author_defaults_to_user_id(server):
    res = await _create(server)
    assert res["handoff"]["created_by"] == USER


async def test_update_records_the_session_name_as_author(server):
    hid = (await _create(server))["handoff"]["id"]
    fake = server.fake_handoffs
    await server._dispatch(
        "context_handoff_update",
        {"user_id": USER, "id": hid, "body": "v2", "author": "synatyx-lead"},
    )
    await server._dispatch(
        "context_handoff_update", {"user_id": USER, "id": hid, "body": "v3"}
    )
    assert fake.authors[(hid, 2)] == "synatyx-lead"
    assert fake.authors[(hid, 3)] == USER


# ── context_brief assignee ───────────────────────────────────────────────────


def _brief_server(handoffs):
    from tests.test_brief import FakePostgresBrief, FakeQdrantBrief

    class _Qdrant(FakeQdrantBrief):
        collection_name = "ctx_synatyx"

    srv = SynatyxMCPServer.__new__(SynatyxMCPServer)
    srv._postgres = FakePostgresBrief(handoffs=handoffs)  # type: ignore[assignment]

    async def _get_services(user_id, project=None):
        return _Qdrant([]), None, None, None, None

    async def get_l4_storage():
        return FakeQdrantBrief([])

    async def get_project(user_id):
        return None

    srv._get_services = _get_services  # type: ignore[method-assign]
    srv._project_manager = types.SimpleNamespace(  # type: ignore[assignment]
        get_l4_storage=get_l4_storage, get_project=get_project
    )
    return srv


def _stored_handoff(slug="pr-c", project="synatyx"):
    from tests.test_brief import _handoff

    return _handoff(slug, project=project, user_id=USER)


async def test_brief_with_assignee_lists_open_handoffs():
    srv = _brief_server([_stored_handoff()])
    res = await srv._dispatch(
        "context_brief", {"user_id": USER, "project": "synatyx", "assignee": "w3"}
    )
    assert [h["slug"] for h in res["open_handoffs"]] == ["pr-c"]
    assert "SECRET BODY" not in str(res)


async def test_brief_without_assignee_has_no_open_handoffs():
    srv = _brief_server([_stored_handoff()])
    res = await srv._dispatch("context_brief", {"user_id": USER, "project": "synatyx"})
    assert "open_handoffs" not in res


async def test_brief_finds_handoffs_whatever_the_project_casing():
    srv = _brief_server([_stored_handoff(project="synatyx")])
    for spelling in ("Synatyx", "synatyx", " SYNATYX "):
        res = await srv._dispatch(
            "context_brief", {"user_id": USER, "project": spelling, "assignee": "w3"}
        )
        assert [h["slug"] for h in res["open_handoffs"]] == ["pr-c"], spelling


async def test_create_unknown_parent_is_invalid_not_not_found(server):
    res = await _create(server, parent_id="nope")
    assert "parent handoff" in res["error"]
    assert res["code"] == "INVALID"


async def test_every_error_keeps_its_prose_and_gains_a_code(server):
    res = await server._dispatch("context_handoff_get", {"user_id": USER, "id": "nope"})
    assert res == {"error": "Handoff not found", "code": "NOT_FOUND"}


async def test_success_results_carry_no_code(server):
    res = await _create(server)
    assert "code" not in res and "error" not in res
