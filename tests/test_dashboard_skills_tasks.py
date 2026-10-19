from __future__ import annotations

from typing import Any

from starlette.applications import Starlette
from starlette.testclient import TestClient

from src.models.skill import Skill
from src.models.task import Task, TaskPriority, TaskStatus
from src.transports.mcp.dashboard import DASHBOARD_ROUTES


class FakePostgres:
    """The skill and task calls the new routes make."""

    def __init__(self, skills: list[Skill], tasks: list[Task]) -> None:
        self.skills = skills
        self.tasks = {t.id: t for t in tasks}
        self.update_calls: list[dict[str, Any]] = []

    async def skill_list_all(
        self, user_id: str | None = None, project: str | None = None, limit: int = 100
    ) -> list[Skill]:
        return [
            s for s in self.skills
            if (user_id is None or s.user_id == user_id)
            and (project is None or s.project == project)
        ][:limit]

    async def skill_get_by_name(
        self, name: str, user_id: str, project: str | None = None
    ) -> Skill | None:
        for s in self.skills:
            if s.user_id == user_id and name in (s.name, s.slug) and (
                project is None or s.project == project
            ):
                return s
        return None

    async def task_update(self, task_id: str, user_id: str, **fields: Any) -> Task | None:
        self.update_calls.append({"task_id": task_id, "user_id": user_id, **fields})
        t = self.tasks.get(task_id)
        if t is None or t.user_id != user_id:
            return None
        for name, value in fields.items():
            if value is not None:
                setattr(t, name, value)
        return t


def _client(postgres: FakePostgres | None) -> TestClient:
    app = Starlette(routes=list(DASHBOARD_ROUTES))
    app.state.qdrant = object() if postgres is not None else None
    app.state.postgres = postgres
    return TestClient(app)


def _skills() -> list[Skill]:
    return [
        Skill(name="Deploy App", description="ship it", content="# steps", user_id="u1",
              project="alpha", frontmatter={"tags": ["ops"]}),
        Skill(name="Write Tests", description="test it", content="# tests", user_id="u2"),
    ]


def _task(**over: Any) -> Task:
    return Task(user_id="u1", title="Old", project="alpha", **over)


# ── skills ───────────────────────────────────────────────────────────────────

def test_skills_list_is_cross_user_and_has_no_content() -> None:
    res = _client(FakePostgres(_skills(), [])).get("/dashboard/api/skills")
    assert res.status_code == 200
    skills = res.json()["skills"]
    assert {s["slug"] for s in skills} == {"deploy-app", "write-tests"}
    assert set(skills[0]) == {
        "slug", "name", "description", "project", "user_id", "updated_at"
    }


def test_skills_list_filters_by_user_and_project() -> None:
    client = _client(FakePostgres(_skills(), []))
    assert [s["slug"] for s in client.get("/dashboard/api/skills?user=u2").json()["skills"]] == [
        "write-tests"
    ]
    assert [
        s["slug"] for s in client.get("/dashboard/api/skills?project=alpha").json()["skills"]
    ] == ["deploy-app"]


def test_skill_detail_returns_content_and_frontmatter() -> None:
    res = _client(FakePostgres(_skills(), [])).get("/dashboard/api/skills/deploy-app?user=u1")
    assert res.status_code == 200
    body = res.json()
    assert body["content"] == "# steps"
    assert body["frontmatter"] == {"tags": ["ops"]}
    assert body["slug"] == "deploy-app"


def test_skill_detail_requires_user() -> None:
    res = _client(FakePostgres(_skills(), [])).get("/dashboard/api/skills/deploy-app")
    assert res.status_code == 400
    assert "user" in res.json()["error"]


def test_skill_detail_unknown_or_other_users_is_404() -> None:
    client = _client(FakePostgres(_skills(), []))
    assert client.get("/dashboard/api/skills/nope?user=u1").status_code == 404
    assert client.get("/dashboard/api/skills/deploy-app?user=u2").status_code == 404


def test_skills_not_ready_is_503() -> None:
    assert _client(None).get("/dashboard/api/skills").status_code == 503


# ── PATCH tasks ──────────────────────────────────────────────────────────────

def test_patch_task_updates_and_returns_the_task() -> None:
    task = _task()
    pg = FakePostgres([], [task])
    res = _client(pg).patch(
        f"/dashboard/api/tasks/{task.id}",
        json={
            "user": "u1", "status": "in_progress", "priority": "high",
            "title": "New", "description": "d", "assignee": "synatyx-w2",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert (body["status"], body["priority"], body["title"]) == ("in_progress", "high", "New")
    assert body["assignee"] == "synatyx-w2"
    assert body["description"] == "d"
    assert pg.update_calls[0]["status"] is TaskStatus.IN_PROGRESS
    assert pg.update_calls[0]["priority"] is TaskPriority.HIGH


def test_patch_task_only_touches_the_given_fields() -> None:
    task = _task()
    pg = FakePostgres([], [task])
    _client(pg).patch(f"/dashboard/api/tasks/{task.id}?user=u1", json={"status": "done"})
    assert pg.update_calls[0] == {"task_id": task.id, "user_id": "u1", "status": TaskStatus.DONE}


def test_patch_task_rejects_bad_enums() -> None:
    task = _task()
    client = _client(FakePostgres([], [task]))
    for payload in ({"status": "nonsense"}, {"priority": "urgent"}):
        res = client.patch(f"/dashboard/api/tasks/{task.id}", json={"user": "u1", **payload})
        assert res.status_code == 400, payload


def test_patch_task_rejects_blank_title_and_non_string_fields() -> None:
    task = _task()
    client = _client(FakePostgres([], [task]))
    for payload in ({"title": "  "}, {"title": 5}, {"assignee": 3}, {"description": None}):
        res = client.patch(f"/dashboard/api/tasks/{task.id}", json={"user": "u1", **payload})
        assert res.status_code == 400, payload


def test_patch_task_unknown_is_404() -> None:
    res = _client(FakePostgres([], [])).patch(
        "/dashboard/api/tasks/nope", json={"user": "u1", "status": "done"}
    )
    assert res.status_code == 404


def test_patch_task_of_another_user_is_404() -> None:
    task = _task()
    res = _client(FakePostgres([], [task])).patch(
        f"/dashboard/api/tasks/{task.id}", json={"user": "u2", "status": "done"}
    )
    assert res.status_code == 404


def test_patch_task_requires_user_and_a_json_object() -> None:
    task = _task()
    client = _client(FakePostgres([], [task]))
    url = f"/dashboard/api/tasks/{task.id}"
    assert client.patch(url, json={"status": "done"}).status_code == 400
    assert client.patch(url, json=[1]).status_code == 400
    assert client.patch(url, content="{nope").status_code == 400


def test_task_list_now_carries_assignee() -> None:
    class ListPostgres(FakePostgres):
        async def task_list_all(self, status: Any = None, limit: int = 50) -> list[Task]:
            return list(self.tasks.values())

    task = _task(assignee="w2")
    res = _client(ListPostgres([], [task])).get("/dashboard/api/tasks")
    assert res.json()["tasks"][0]["assignee"] == "w2"
