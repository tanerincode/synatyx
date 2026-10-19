from __future__ import annotations

import uuid
from typing import Any

from starlette.applications import Starlette
from starlette.testclient import TestClient

from src.models.relation import MemoryRelation
from src.transports.mcp.dashboard import DASHBOARD_ROUTES


def _id(n: int) -> str:
    return str(uuid.UUID(int=n))


class FakeStore:
    """One collection; the get_item/set_pinned/... contract of QdrantStorage."""

    def __init__(self, items: dict[str, dict[str, Any]]) -> None:
        self.items = items

    async def get_item(self, item_id: str) -> dict[str, Any] | None:
        p = self.items.get(item_id)
        return {**p, "_id": item_id} if p else None

    async def set_pinned(self, item_id: str, pinned: bool) -> None:
        self.items[item_id]["is_pinned"] = pinned

    async def deprecate(self, item_id: str, reason: str | None = None) -> None:
        p = self.items[item_id]
        p["is_deprecated"] = True
        p["deprecated_at"] = "2026-01-01T00:00:00+00:00"
        if reason:
            p["deprecated_reason"] = reason

    async def undeprecate(self, item_id: str) -> None:
        p = self.items[item_id]
        p["is_deprecated"] = False
        p.pop("deprecated_at", None)
        p.pop("deprecated_reason", None)

    async def scan_all_items(
        self, memory_layer: Any = None, include_deprecated: bool = False,
        limit: int = 1000, offset: Any = None, with_vectors: bool = False,
    ) -> tuple[list[dict[str, Any]], None]:
        out = [{**p, "_id": i} for i, p in self.items.items()]
        if not include_deprecated:
            out = [p for p in out if not p.get("is_deprecated")]
        if memory_layer:
            out = [p for p in out if p.get("memory_layer") == memory_layer.value]
        return out[:limit], None


class FakeQdrant:
    def __init__(self, collections: dict[str, dict[str, dict[str, Any]]]) -> None:
        self.collections = collections

    async def get_all_collections(self) -> list[str]:
        return list(self.collections) + ["other_thing"]

    def scoped(self, name: str) -> FakeStore:
        return FakeStore(self.collections.setdefault(name, {}))


class FakePostgres:
    def __init__(self, relations: list[MemoryRelation]) -> None:
        self.relations = relations

    async def relation_list_all(
        self, item_ids: list[str], limit: int = 500
    ) -> list[MemoryRelation]:
        return [
            r for r in self.relations
            if r.source_item_id in item_ids or r.target_item_id in item_ids
        ][:limit]


def _rel(src: str, dst: str, kind: str) -> MemoryRelation:
    return MemoryRelation(
        user_id="u1", source_item_id=src, target_item_id=dst, relation_type=kind
    )


def _p(content: str, created: str, importance: float = 0.5, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "user_id": "u1", "content": content, "memory_layer": "L3",
        "importance": importance, "is_pinned": False, "is_deprecated": False,
        "project": "synatyx", "metadata": {}, "created_at": created,
    }
    base.update(kw)
    return base


def _client() -> tuple[TestClient, dict[str, dict[str, dict[str, Any]]]]:
    a, b, c, d = _id(1), _id(2), _id(3), _id(4)
    collections = {
        "ctx_synatyx": {
            a: _p("Alpha fact about Redis", "2026-01-01T10:00:00+00:00", 0.9,
                  metadata={"origin": "user-stated", "type": "decision", "confidence": 0.8,
                            "fact_type": "config", "files": ["a.py"]}),
            b: _p("Beta note on postgres", "2026-02-01T10:00:00+00:00", 0.2,
                  metadata={"origin": "agent-inferred"}, user_id="u2"),
            c: _p("Gamma ALPHA again", "2026-03-01T10:00:00+00:00", 0.5,
                  memory_layer="L2"),
            d: _p("Deprecated delta", "2026-04-01T10:00:00+00:00", is_deprecated=True),
        },
        "ctx_users": {
            _id(9): _p("A global preference that is long " + "x" * 100,
                       "2026-01-01T00:00:00+00:00", memory_layer="L4"),
        },
    }
    relations = [
        _rel(a, b, "related_to"),
        _rel(c, a, "supersedes"),
        _rel(a, _id(9), "part_of"),
        _rel(a, _id(77), "used_for"),
    ]
    app = Starlette(routes=list(DASHBOARD_ROUTES))
    app.state.qdrant = FakeQdrant(collections)
    app.state.postgres = FakePostgres(relations)
    return TestClient(app), collections


def test_detail_shape_and_related_both_directions() -> None:
    client, _ = _client()
    res = client.get(f"/dashboard/api/items/{_id(1)}", params={"collection": "ctx_synatyx"})
    assert res.status_code == 200
    d = res.json()
    assert d["id"] == _id(1)
    assert d["content"] == "Alpha fact about Redis"
    assert (d["confidence"], d["fact_type"], d["files"]) == (0.8, "config", ["a.py"])
    assert d["possibly_stale"] is False

    rel = {(r["id"], r["type"], r["direction"]): r["label"] for r in d["related"]}
    assert rel[(_id(2), "related_to", "out")] == "Beta note on postgres"
    assert rel[(_id(3), "supersedes", "in")] == "Gamma ALPHA again"
    # label of an L4 item in ctx_users, cut to ~80 chars
    label = rel[(_id(9), "part_of", "out")]
    assert label.startswith("A global preference") and len(label) == 80
    # dangling edge: kept, empty label
    assert rel[(_id(77), "used_for", "out")] == ""
    assert all(set(r) == {"id", "type", "direction", "label"} for r in d["related"])


def test_detail_404_and_bad_collection() -> None:
    client, _ = _client()
    assert client.get(
        f"/dashboard/api/items/{_id(50)}", params={"collection": "ctx_synatyx"}
    ).status_code == 404
    assert client.get(
        f"/dashboard/api/items/{_id(1)}", params={"collection": "other_thing"}
    ).status_code == 400
    assert client.get(f"/dashboard/api/items/{_id(1)}").status_code == 400


def test_detail_reports_stale_files(tmp_path: Any) -> None:
    client, cols = _client()
    f = tmp_path / "f.txt"
    f.write_text("v1")
    from src.core.staleness import compute_file_hashes

    cols["ctx_synatyx"][_id(3)]["metadata"] = {"file_hashes": compute_file_hashes([str(f)])}
    f.write_text("v2")
    d = client.get(f"/dashboard/api/items/{_id(3)}", params={"collection": "ctx_synatyx"}).json()
    assert d["possibly_stale"] is True and d["stale_files"] == [str(f)]


def test_pin_unpin_round_trip() -> None:
    client, cols = _client()
    base = f"/dashboard/api/items/{_id(2)}"
    res = client.post(f"{base}/pin", params={"collection": "ctx_synatyx"})
    assert res.status_code == 200 and res.json()["is_pinned"] is True
    assert cols["ctx_synatyx"][_id(2)]["is_pinned"] is True
    res = client.post(f"{base}/unpin", params={"collection": "ctx_synatyx"})
    assert res.json()["is_pinned"] is False


def test_deprecate_then_restore() -> None:
    client, cols = _client()
    base = f"/dashboard/api/items/{_id(2)}"
    res = client.post(f"{base}/deprecate", params={"collection": "ctx_synatyx"},
                      json={"reason": "outdated"})
    d = res.json()
    assert d["is_deprecated"] is True and d["deprecated_reason"] == "outdated"
    assert d["deprecated_at"]

    res = client.post(f"{base}/restore", params={"collection": "ctx_synatyx"})
    d = res.json()
    assert d["is_deprecated"] is False
    assert d["deprecated_at"] is None and d["deprecated_reason"] is None
    assert "deprecated_at" not in cols["ctx_synatyx"][_id(2)]


def test_deprecate_without_body_and_action_errors() -> None:
    client, _ = _client()
    base = f"/dashboard/api/items/{_id(2)}"
    res = client.post(f"{base}/deprecate", params={"collection": "ctx_synatyx"})
    assert res.status_code == 200 and res.json()["deprecated_reason"] is None
    assert client.post(f"{base}/explode", params={"collection": "ctx_synatyx"}).status_code == 404
    assert client.post(
        f"/dashboard/api/items/{_id(50)}/pin", params={"collection": "ctx_synatyx"}
    ).status_code == 404
    assert client.post(f"{base}/pin", params={"collection": "nope"}).status_code == 400
    assert client.get(f"{base}/pin", params={"collection": "ctx_synatyx"}).status_code == 405


def _ids(client: TestClient, **params: str) -> list[str]:
    res = client.get("/dashboard/api/items", params={"collection": "ctx_synatyx", **params})
    assert res.status_code == 200, res.text
    return [i["id"] for i in res.json()["items"]]


def test_list_defaults_unchanged_newest_first_with_total() -> None:
    client, _ = _client()
    res = client.get("/dashboard/api/items", params={"collection": "ctx_synatyx"}).json()
    assert [i["id"] for i in res["items"]] == [_id(3), _id(2), _id(1)]  # deprecated hidden
    assert res["count"] == 3 and res["total"] == 3 and res["capped"] is False
    for key in ("collection", "generated_at", "items"):
        assert key in res
    for key in ("id", "content", "truncated", "memory_layer", "importance", "is_pinned",
                "is_deprecated", "origin", "type", "checkpoint_name", "user_id",
                "session_id", "project", "created_at"):
        assert key in res["items"][0]


def test_list_filters() -> None:
    client, _ = _client()
    assert _ids(client, q="alpha") == [_id(3), _id(1)]  # case-insensitive
    assert _ids(client, origin="user-stated") == [_id(1)]
    assert _ids(client, type="decision") == [_id(1)]
    assert _ids(client, layer="L2") == [_id(3)]
    assert _ids(client, user="u2") == [_id(2)]
    assert _ids(client, include_deprecated="true")[0] == _id(4)
    assert _ids(client, **{"from": "2026-02-01T00:00:00+00:00"}) == [_id(3), _id(2)]
    assert _ids(client, to="2026-02-01T10:00:00+00:00") == [_id(2), _id(1)]
    assert _ids(client, **{"from": "2026-01-15", "to": "2026-02-15"}) == [_id(2)]
    assert _ids(client, q="alpha", origin="user-stated") == [_id(1)]


def test_list_stale_filter(tmp_path: Any) -> None:
    client, cols = _client()
    f = tmp_path / "f.txt"
    f.write_text("v1")
    from src.core.staleness import compute_file_hashes

    cols["ctx_synatyx"][_id(2)]["metadata"] = {"file_hashes": compute_file_hashes([str(f)])}
    f.write_text("v2")
    assert _ids(client, stale="true") == [_id(2)]
    assert _ids(client, stale="false") == [_id(3), _id(1)]
    res = client.get("/dashboard/api/items", params={"collection": "ctx_synatyx"}).json()
    assert {i["id"]: i["possibly_stale"] for i in res["items"]}[_id(2)] is True


def test_list_sort_offset_total() -> None:
    client, _ = _client()
    assert _ids(client, sort="created_at") == [_id(1), _id(2), _id(3)]
    assert _ids(client, sort="-importance") == [_id(1), _id(3), _id(2)]
    assert _ids(client, sort="importance") == [_id(2), _id(3), _id(1)]

    res = client.get(
        "/dashboard/api/items",
        params={"collection": "ctx_synatyx", "limit": "1", "offset": "1", "sort": "created_at"},
    ).json()
    assert [i["id"] for i in res["items"]] == [_id(2)]
    assert res["count"] == 1 and res["total"] == 3 and res["offset"] == 1
    past = client.get(
        "/dashboard/api/items", params={"collection": "ctx_synatyx", "offset": "10"}
    ).json()
    assert past["items"] == [] and past["total"] == 3


def test_list_bad_params() -> None:
    client, _ = _client()
    for params in (
        {"sort": "nope"}, {"from": "yesterday"}, {"to": "x"}, {"stale": "maybe"},
        {"offset": "abc"}, {"limit": "abc"}, {"layer": "L9"},
    ):
        res = client.get("/dashboard/api/items", params={"collection": "ctx_synatyx", **params})
        assert res.status_code == 400, params
    assert client.get("/dashboard/api/items", params={"collection": "nope"}).status_code == 400
