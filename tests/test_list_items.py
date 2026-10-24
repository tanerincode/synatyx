"""context_list must return every matching item, newest first, and must not
filter the user-global L4 layer by project.

Uses a real QdrantStorage over an in-memory client that evaluates filters and
paginates scroll() like Qdrant (point-id order, `limit` per page, next offset).
"""
from __future__ import annotations

import types
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src.models.context import ContextItem
from src.models.memory_layer import MemoryLayer
from src.storage.qdrant import QdrantStorage
from src.transports.mcp.server import SynatyxMCPServer

USER = "tombastaner"
T0 = datetime(2026, 10, 1, tzinfo=UTC)


class PagingClient:
    """Minimal AsyncQdrantClient stand-in: upsert + filtered, paged scroll."""

    def __init__(self) -> None:
        self.points: dict[str, Any] = {}

    async def upsert(self, collection_name: str, points: list[Any]) -> None:
        for p in points:
            self.points[str(p.id)] = p

    async def scroll(
        self,
        collection_name: str,
        scroll_filter: Any = None,
        limit: int = 10,
        offset: Any = None,
        with_payload: bool = True,
        with_vectors: bool = False,
    ) -> tuple[list[Any], Any]:
        matching = sorted(
            (
                p
                for p in self.points.values()
                if all(
                    p.payload.get(c.key) == c.match.value
                    for c in (scroll_filter.must if scroll_filter else [])
                )
            ),
            key=lambda p: str(p.id),
        )
        start = 0
        if offset is not None:
            start = next(i for i, p in enumerate(matching) if str(p.id) == offset)
        page = matching[start : start + limit]
        nxt = str(matching[start + limit].id) if start + limit < len(matching) else None
        return page, nxt


def _storage(collection: str) -> QdrantStorage:
    storage = QdrantStorage(host="localhost", port=6333, collection_name=collection)
    storage._client = PagingClient()  # type: ignore[assignment]
    return storage


def _item(
    layer: MemoryLayer = MemoryLayer.L3,
    age_minutes: int = 0,
    metadata: dict[str, Any] | None = None,
) -> ContextItem:
    return ContextItem(
        id=str(uuid.uuid4()),
        user_id=USER,
        content=f"{layer.value} item {age_minutes}",
        memory_layer=layer,
        embedding=[0.1] * 4,
        metadata=metadata or {},
        created_at=T0 + timedelta(minutes=age_minutes),
    )


# -- storage: pagination and ordering ---------------------------------------


async def test_list_items_pages_past_the_first_scroll_page() -> None:
    storage = _storage("ctx_kai")
    items = [_item(age_minutes=i) for i in range(30)]
    for it in items:
        await storage.upsert(it)

    # Force small pages so the 30 items span several scroll calls.
    got = await storage.list_items(user_id=USER, limit=1000, page_size=7)

    assert {i.id for i in got} == {i.id for i in items}


async def test_item_stored_after_a_full_page_is_listed_first() -> None:
    storage = _storage("ctx_kai")
    for i in range(20):
        await storage.upsert(_item(age_minutes=i))
    newest = _item(age_minutes=999)
    await storage.upsert(newest)

    got = await storage.list_items(user_id=USER, limit=5, page_size=7)

    assert len(got) == 5
    assert got[0].id == newest.id  # newest first, whatever its point id


async def test_list_items_sorted_newest_first_and_limit_applied() -> None:
    storage = _storage("ctx_kai")
    for i in (5, 1, 9, 3):
        await storage.upsert(_item(age_minutes=i))

    got = await storage.list_items(user_id=USER, limit=3)

    assert [i.created_at for i in got] == sorted((i.created_at for i in got), reverse=True)
    assert [int((i.created_at - T0).total_seconds() // 60) for i in got] == [9, 5, 3]


async def test_checkpoints_only_still_filters_after_paging() -> None:
    storage = _storage("ctx_kai")
    for i in range(10):
        await storage.upsert(_item(age_minutes=i))
    cp = _item(age_minutes=-5, metadata={"checkpoint_name": "m1"})
    await storage.upsert(cp)

    got = await storage.list_items(
        user_id=USER, checkpoints_only=True, limit=10, page_size=3
    )

    assert [i.id for i in got] == [cp.id]


# -- server: L4 is user-global ----------------------------------------------


@pytest.fixture
def server() -> SynatyxMCPServer:
    project = _storage("ctx_kai")
    users = _storage("ctx_users")
    srv = SynatyxMCPServer.__new__(SynatyxMCPServer)
    srv.project_storage = project  # type: ignore[attr-defined]
    srv.users_storage = users  # type: ignore[attr-defined]

    async def _get_services(user_id: str, project_arg: str | None = None) -> Any:
        return project, None, None, None, None

    async def _get_l4_services() -> Any:
        return users, None, None

    srv._get_services = _get_services  # type: ignore[method-assign]
    srv._get_l4_services = _get_l4_services  # type: ignore[method-assign]
    return srv


async def test_l4_list_ignores_project_filter(server: SynatyxMCPServer) -> None:
    # Stored with project=kai but no metadata.project: the ctx_users point has
    # payload.project None, so a project filter would hide it.
    rule = _item(MemoryLayer.L4, metadata={"tags": ["rule"]})
    await server.users_storage.upsert(rule)  # type: ignore[attr-defined]

    res = await server._dispatch(
        "context_list",
        {"user_id": USER, "project": "kai", "memory_layer": "L4", "limit": 1000},
    )

    assert [i["id"] for i in res["items"]] == [rule.id]


async def test_l4_graph_ignores_project_filter(server: SynatyxMCPServer) -> None:
    server._get_relation_service = _fake_relations  # type: ignore[method-assign]
    server._postgres = types.SimpleNamespace(  # type: ignore[assignment]
        relation_list=_no_edges
    )
    rule = _item(MemoryLayer.L4)
    await server.users_storage.upsert(rule)  # type: ignore[attr-defined]

    res = await server._dispatch(
        "context_visualize",
        {"user_id": USER, "project": "kai", "memory_layer": "L4"},
    )

    assert res["node_count"] == 1


async def _fake_relations(*_a: Any, **_k: Any) -> Any:
    return None


async def _no_edges(**_k: Any) -> list[Any]:
    return []


async def test_project_list_uses_explicit_project_and_pages(
    server: SynatyxMCPServer,
) -> None:
    for i in range(300):
        await server.project_storage.upsert(  # type: ignore[attr-defined]
            _item(age_minutes=i, metadata={"project": "kai"})
        )
    fresh = _item(age_minutes=5000, metadata={"project": "kai"})
    await server.project_storage.upsert(fresh)  # type: ignore[attr-defined]

    res = await server._dispatch(
        "context_list", {"user_id": USER, "project": "kai", "limit": 1000}
    )

    assert res["count"] == 301
    assert res["items"][0]["id"] == fresh.id
