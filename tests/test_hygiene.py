from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from src.config import settings
from src.core.hygiene import DASHBOARD_WARN_DAYS, GC_STATS_WARN_DAYS, HygieneService
from src.core.staleness import compute_file_hashes
from src.models.relation import MemoryRelation
from src.transports.mcp.dashboard import DASHBOARD_ROUTES, api_hygiene
from src.transports.mcp.server import SynatyxMCPServer

NOW = datetime.now(UTC)


def _ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def _item(item_id: str, layer: str = "L2", *, created: float = 1, **extra: Any) -> dict[str, Any]:
    return {
        "_id": item_id,
        "user_id": "u1",
        "content": f"content {item_id}",
        "memory_layer": layer,
        "importance": 0.0,
        "is_pinned": False,
        "is_deprecated": False,
        "created_at": _ago(created),
        "metadata": {},
        **extra,
    }


def _gc_items() -> list[dict[str, Any]]:
    """Defaults: L2 ttl 30d, L3 ttl 90d, grace 30d; importance 0 so ttl = base."""
    return [
        _item("a", "L2", created=20),                    # 10d left -> expiring
        _item("b", "L2", created=5),                     # 25d left
        _item("c", "L2", created=40),                    # already past its ttl
        _item("d", "L3", created=80),                    # 10d left -> expiring
        _item("e", "L4"),                                # immune layer
        _item("f", "L3", is_pinned=True),                # pinned
        _item("g", "L1"),                                # no base ttl
        _item("h", "L2", is_deprecated=True, deprecated_at=_ago(40)),  # past grace
        _item("i", "L2", is_deprecated=True, deprecated_at=_ago(5)),
        _item("j", "L2", is_deprecated=True),            # no deprecated_at
        _item("k", "L2", created=100, last_accessed_at=_ago(2)),  # touched recently
    ]


class FakeQdrant:
    def __init__(self, collections: dict[str, list[dict[str, Any]]]) -> None:
        self._collections = collections
        self._scope = next(iter(collections), "ctx_default")

    async def get_all_collections(self) -> list[str]:
        return list(self._collections) + ["ctx_proj__index"]

    def scoped(self, collection_name: str) -> FakeQdrant:
        clone = FakeQdrant(self._collections)
        clone._scope = collection_name
        return clone

    async def scan_all_items(self, include_deprecated: bool = False, limit: int = 1000, **_: Any):
        items = [dict(i) for i in self._collections[self._scope]]
        if not include_deprecated:
            items = [i for i in items if not i.get("is_deprecated")]
        return items[:limit], None


class FakePostgres:
    def __init__(self, relations: list[MemoryRelation] | None = None) -> None:
        self.relations = relations or []

    async def relation_list_all(self, item_ids: list[str], limit: int = 500):
        wanted = set(item_ids)
        return [
            r for r in self.relations
            if r.source_item_id in wanted or r.target_item_id in wanted
        ][:limit]


def _service(relations: list[MemoryRelation] | None = None) -> HygieneService:
    return HygieneService(FakeQdrant({"ctx_p": []}), FakePostgres(relations), settings.gc)


def _edge(a: str, b: str, kind: str = "alternative_to", **meta: Any) -> MemoryRelation:
    return MemoryRelation(
        user_id="u1", source_item_id=a, target_item_id=b, relation_type=kind, metadata=meta
    )


# ---- gc counts -------------------------------------------------------------------


def test_gc_counts_classify_every_kind_of_item():
    counts = _service().gc_counts(_gc_items(), warn_days=GC_STATS_WARN_DAYS, now=NOW)

    assert counts.total == 11
    assert counts.protected == 3        # e, f, g
    assert counts.expiring_soon == 2    # a, d
    assert counts.deprecated == 3       # h, i, j
    assert counts.pending_hard_delete == 1  # h


def test_a_narrower_window_counts_fewer_expiring_items():
    items = [_item("soon", created=25), _item("later", created=18)]  # 5d and 12d left
    svc = _service()

    assert svc.gc_counts(items, warn_days=DASHBOARD_WARN_DAYS, now=NOW).expiring_soon == 1
    assert svc.gc_counts(items, warn_days=GC_STATS_WARN_DAYS, now=NOW).expiring_soon == 2


async def test_context_gc_stats_output_is_unchanged():
    server = SynatyxMCPServer.__new__(SynatyxMCPServer)
    qdrant = FakeQdrant({"ctx_p": _gc_items()})
    server._postgres = FakePostgres()  # type: ignore[assignment]

    async def _get_services(user_id: str, project: str | None = None):
        return qdrant, None, None, None, None

    server._get_services = _get_services  # type: ignore[method-assign]

    result = await server._dispatch("context_gc_stats", {"user_id": "u1", "project": "p"})

    assert result == {
        "total_items": 11,
        "protected": 3,
        "expiring_soon_14d": 2,
        "already_deprecated": 3,
        "pending_hard_delete": 1,
        "gc_enabled": settings.gc.enabled,
        "l2_base_ttl_days": settings.gc.l2_base_ttl_days,
        "l3_base_ttl_days": settings.gc.l3_base_ttl_days,
        "grace_period_days": settings.gc.grace_period_days,
    }


# ---- duplicates ------------------------------------------------------------------


async def test_duplicates_come_from_alternative_to_edges_with_stored_similarity():
    items = [_item("a"), _item("b"), _item("c"), _item("d")]
    svc = _service([
        _edge("a", "b", auto=True, similarity=0.95),
        _edge("c", "d"),                                  # manual edge: no similarity
        _edge("a", "c", kind="related_to"),               # not a duplicate edge
    ])

    pairs = await svc.duplicates(items)

    assert [(p["a"]["_id"], p["b"]["_id"], p["similarity"]) for p in pairs] == [
        ("a", "b", 0.95),
        ("c", "d", None),
    ]


async def test_a_pair_is_reported_once_and_skips_deprecated_or_missing_items():
    items = [_item("a"), _item("b"), _item("c", is_deprecated=True)]
    svc = _service([
        _edge("a", "b", similarity=0.9),
        _edge("b", "a", similarity=0.9),     # same pair, other direction
        _edge("a", "c", similarity=0.99),    # c is deprecated: already resolved
        _edge("a", "zzz", similarity=0.99),  # other end not in this collection
    ])

    pairs = await svc.duplicates(items)

    assert len(pairs) == 1
    assert {pairs[0]["a"]["_id"], pairs[0]["b"]["_id"]} == {"a", "b"}


# ---- stale -----------------------------------------------------------------------


def test_stale_items_are_those_whose_files_changed(tmp_path: Path):
    watched = tmp_path / "watched.txt"
    watched.write_text("v1")
    hashes = compute_file_hashes([str(watched)])
    fresh = _item("fresh", metadata={"file_hashes": hashes})
    plain = _item("plain")
    watched.write_text("v2")
    changed = _item("changed", metadata={"file_hashes": hashes})
    gone = _item("gone", metadata={"file_hashes": {str(tmp_path / "gone.txt"): "abc"}})
    watched_deprecated = _item("old", is_deprecated=True, metadata={"file_hashes": hashes})

    stale = HygieneService.stale_items([fresh, plain, changed, gone, watched_deprecated])

    assert [s["_id"] for s in stale] == ["fresh", "changed", "gone"]
    assert all(s["possibly_stale"] for s in stale)
    assert stale[1]["stale_files"] == [str(watched)]


def test_stale_items_are_capped(tmp_path: Path):
    meta = {"file_hashes": {str(tmp_path / "missing"): "abc"}}
    items = [_item(f"s{i}", metadata=meta) for i in range(80)]

    assert len(HygieneService.stale_items(items)) == 50
    assert len(HygieneService.stale_items(items, limit=5)) == 5


# ---- route -----------------------------------------------------------------------


def _client(qdrant: Any, postgres: Any) -> TestClient:
    app = Starlette(routes=[Route("/dashboard/api/hygiene", api_hygiene)])
    app.state.qdrant = qdrant
    app.state.postgres = postgres
    return TestClient(app)


def test_route_is_registered_in_the_dashboard_routes():
    assert "/dashboard/api/hygiene" in {r.path for r in DASHBOARD_ROUTES}


def test_route_returns_503_when_storage_is_not_ready():
    app = Starlette(routes=[Route("/dashboard/api/hygiene", api_hygiene)])
    response = TestClient(app).get("/dashboard/api/hygiene?collection=ctx_p")
    assert response.status_code == 503

    app.state.qdrant = FakeQdrant({"ctx_p": []})  # postgres still missing
    assert TestClient(app).get("/dashboard/api/hygiene?collection=ctx_p").status_code == 503


def test_route_rejects_unknown_and_index_collections():
    client = _client(FakeQdrant({"ctx_p": []}), FakePostgres())
    assert client.get("/dashboard/api/hygiene?collection=nope").status_code == 400
    assert client.get("/dashboard/api/hygiene?collection=ctx_proj__index").status_code == 400
    assert client.get("/dashboard/api/hygiene").status_code == 400


def test_route_shape_and_values(tmp_path: Path):
    items = _gc_items() + [
        _item("dup1", content="first"),
        _item("dup2", content="second"),
        _item("stale", metadata={"file_hashes": {str(tmp_path / "gone"): "abc"}}),
    ]
    relations = [_edge("dup1", "dup2", auto=True, similarity=0.93)]
    client = _client(FakeQdrant({"ctx_p": items}), FakePostgres(relations))

    body = client.get("/dashboard/api/hygiene?collection=ctx_p").json()

    assert set(body) == {
        "collection", "generated_at", "expiring_7d", "deprecated", "pending_hard_delete",
        "protected", "duplicates", "stale",
    }
    assert body["collection"] == "ctx_p"
    assert body["deprecated"] == 3
    assert body["pending_hard_delete"] == 1
    assert body["protected"] == 3
    assert body["expiring_7d"] == 0   # a and d have 10d left, outside the 7d window
    assert len(body["duplicates"]) == 1
    dup = body["duplicates"][0]
    assert set(dup) == {"a", "b", "similarity"}
    assert dup["similarity"] == 0.93
    assert {dup["a"]["id"], dup["b"]["id"]} == {"dup1", "dup2"}
    assert [s["id"] for s in body["stale"]] == ["stale"]
    assert body["stale"][0]["possibly_stale"] is True


def test_route_filters_by_user():
    items = [_item("mine"), {**_item("theirs"), "user_id": "u2", "is_deprecated": True}]
    client = _client(FakeQdrant({"ctx_p": items}), FakePostgres())

    assert client.get("/dashboard/api/hygiene?collection=ctx_p&user=u1").json()["deprecated"] == 0
    assert client.get("/dashboard/api/hygiene?collection=ctx_p&user=u2").json()["deprecated"] == 1

