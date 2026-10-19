from __future__ import annotations

from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

import src.transports.mcp.server as server_mod
from src.transports.mcp.server import SynatyxMCPServer
from src.transports.rest.v1 import routes
from tests.test_handoff_tools import FakeHandoffService, FakeTaskStorage


class RealToolsSynatyx:
    """run_tool backed by the real _dispatch and a fake HandoffService.

    The routes only delegate, so the useful test is the whole path: request,
    tool, error dict, envelope.
    """

    def __init__(self) -> None:
        self.server = SynatyxMCPServer.__new__(SynatyxMCPServer)
        self.server._postgres = FakeTaskStorage()  # type: ignore[assignment]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def run_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        try:
            return await self.server._dispatch(name, arguments)
        except Exception as exc:
            return {"error": str(exc), "tool": name, "error_type": type(exc).__name__}


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    fake = FakeHandoffService()
    monkeypatch.setattr(server_mod, "HandoffService", lambda storage: fake)
    app = Starlette(routes=list(routes))
    app.state.synatyx = RealToolsSynatyx()
    app.state.qdrant = None
    return TestClient(app)


def _create(http: TestClient, **over: Any) -> dict[str, Any]:
    payload = {
        "userId": "u1", "project": "Synatyx", "title": "Build routes",
        "body": "SECRET BODY", "assignee": "w2", **over,
    }
    res = http.post("/v1/handoffs", json=payload)
    assert res.status_code == 201, res.text
    handoff: dict[str, Any] = res.json()["handoff"]
    return handoff


def test_create_returns_camel_case_metadata_without_body(http):
    h = _create(http, createdBy="synatyx-lead", taskIds=["t1"])
    assert h["slug"] == "build-routes"
    assert h["status"] == "assigned"
    assert h["createdBy"] == "synatyx-lead"
    assert h["taskIds"] == ["t1"]
    assert "created_by" not in h
    assert "body" not in h


def test_create_missing_fields_names_them_all(http):
    res = http.post("/v1/handoffs", json={"userId": "u1"})
    assert res.status_code == 400
    err = res.json()["error"]
    assert err["code"] == "INVALID_REQUEST"
    assert err["details"]["missing"] == ["project", "title", "body"]


def test_create_slug_clash_is_409(http):
    _create(http)
    res = http.post("/v1/handoffs", json={
        "userId": "u1", "project": "synatyx", "title": "Build routes", "body": "x",
    })
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "INVALID_REQUEST"


def test_create_bad_status_is_400(http):
    res = http.post("/v1/handoffs", json={
        "userId": "u1", "project": "p", "title": "t", "body": "b", "status": "nonsense",
    })
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_REQUEST"


def test_create_unknown_parent_is_400_not_404(http):
    res = http.post("/v1/handoffs", json={
        "userId": "u1", "project": "p", "title": "t", "body": "b", "parentId": "nope",
    })
    assert res.status_code == 400


def test_create_task_ids_must_be_an_array(http):
    res = http.post("/v1/handoffs", json={
        "userId": "u1", "project": "p", "title": "t", "body": "b", "taskIds": "t1",
    })
    assert res.status_code == 400


def test_get_returns_body_and_reports(http):
    hid = _create(http)["id"]
    http.post(f"/v1/handoffs/{hid}/reports", json={"userId": "u1", "agent": "w2", "text": "hi"})
    res = http.get(f"/v1/handoffs/{hid}", params={"userId": "u1"})
    assert res.status_code == 200
    data = res.json()
    assert data["handoff"]["body"] == "SECRET BODY"
    assert data["reports"][0]["text"] == "hi"
    assert data["reports"][0]["handoffId"] == hid


def test_get_older_version(http):
    hid = _create(http)["id"]
    http.patch(f"/v1/handoffs/{hid}", json={"userId": "u1", "body": "second"})
    res = http.get(f"/v1/handoffs/{hid}", params={"userId": "u1", "version": 1})
    assert res.json()["handoff"]["body"] == "SECRET BODY"


def test_get_bad_version_is_400(http):
    hid = _create(http)["id"]
    res = http.get(f"/v1/handoffs/{hid}", params={"userId": "u1", "version": "x"})
    assert res.status_code == 400


def test_get_not_found_is_404(http):
    res = http.get("/v1/handoffs/nope", params={"userId": "u1"})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_list_filters_and_has_no_body(http):
    _create(http)
    _create(http, title="Other", assignee="w3")
    res = http.get("/v1/handoffs", params={"userId": "u1", "assignee": "w3"})
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 1
    assert data["handoffs"][0]["title"] == "Other"
    assert "SECRET BODY" not in res.text


def test_list_bad_status_is_400(http):
    res = http.get("/v1/handoffs", params={"userId": "u1", "status": "nonsense"})
    assert res.status_code == 400


def test_mine_is_not_captured_by_the_id_route(http):
    open_id = _create(http)["id"]
    done_id = _create(http, title="Finished")["id"]
    http.patch(f"/v1/handoffs/{done_id}", json={"userId": "u1", "status": "done"})
    res = http.get("/v1/handoffs/mine", params={"userId": "u1", "assignee": "w2"})
    assert res.status_code == 200
    assert [h["id"] for h in res.json()["handoffs"]] == [open_id]
    assert "SECRET BODY" not in res.text


def test_mine_requires_assignee(http):
    res = http.get("/v1/handoffs/mine", params={"userId": "u1"})
    assert res.status_code == 400
    assert res.json()["error"]["details"]["missing"] == ["assignee"]


def test_update_bumps_version(http):
    hid = _create(http)["id"]
    res = http.patch(f"/v1/handoffs/{hid}", json={
        "userId": "u1", "body": "new", "title": "Renamed", "author": "synatyx-lead",
    })
    assert res.status_code == 200
    h = res.json()["handoff"]
    assert h["version"] == 2
    assert h["title"] == "Renamed"
    assert "body" not in h


def test_update_not_found_is_404(http):
    res = http.patch("/v1/handoffs/nope", json={"userId": "u1", "title": "x"})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_report_moves_status(http):
    hid = _create(http)["id"]
    res = http.post(f"/v1/handoffs/{hid}/reports", json={
        "userId": "u1", "agent": "w2", "text": "finished", "status": "done",
    })
    assert res.status_code == 201
    data = res.json()
    assert data["report"]["statusAfter"] == "done"
    assert data["handoff"]["status"] == "done"


def test_report_requires_agent_and_text(http):
    hid = _create(http)["id"]
    res = http.post(f"/v1/handoffs/{hid}/reports", json={"userId": "u1"})
    assert res.status_code == 400
    assert res.json()["error"]["details"]["missing"] == ["agent", "text"]


def test_report_not_found_is_404(http):
    res = http.post("/v1/handoffs/nope/reports", json={
        "userId": "u1", "agent": "w2", "text": "x",
    })
    assert res.status_code == 404


def test_storage_failure_is_a_502_not_a_404(http, monkeypatch):
    async def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(FakeHandoffService, "get", boom)
    res = http.get("/v1/handoffs/anything", params={"userId": "u1"})
    assert res.status_code == 502
    assert res.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"


def test_not_ready_before_the_server_exists():
    app = Starlette(routes=list(routes))
    app.state.synatyx = None
    res = TestClient(app).get("/v1/handoffs", params={"userId": "u1"})
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "NOT_READY"


def test_mapping_follows_the_code_not_the_prose():
    from src.transports.rest.v1 import _handoff_failed

    # Prose that would have fooled the old substring matching.
    not_found = _handoff_failed({"error": "something else entirely", "code": "NOT_FOUND"})
    conflict = _handoff_failed({"error": "item not found here", "code": "CONFLICT"})
    invalid = _handoff_failed({"error": "already exists and not found", "code": "INVALID"})
    assert not_found is not None and not_found.status_code == 404
    assert conflict is not None and conflict.status_code == 409
    assert invalid is not None and invalid.status_code == 400
