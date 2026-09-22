from __future__ import annotations

from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from src.transports.rest import v1
from src.transports.rest.v1 import routes


class FakeSynatyx:
    """Records what the routes ask for and returns canned results."""

    def __init__(
        self,
        *,
        tool_result: dict[str, Any] | None = None,
        ingest_result: dict[str, Any] | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._tool_result = tool_result or {"context_items": []}
        self._ingest_result = ingest_result or {
            "source_id": "src-1",
            "source": "https://help.example.com/a",
            "chunks_stored": 3,
            "chunks_failed": 0,
            "total_chunks": 3,
        }
        self._raises = raises

    async def run_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        if self._raises is not None:
            raise self._raises
        return self._tool_result

    async def ingest_source(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("ingest_source", kwargs))
        if self._raises is not None:
            raise self._raises
        return self._ingest_result

    async def deprecate_source(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("deprecate_source", kwargs))
        return {"source_id": kwargs["source_id"], "deprecated": 4}

    async def summarize_session(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("summarize_session", kwargs))
        # The shape summarize_session actually returns — one shape, both transports.
        return {"summary": "they asked about billing", "keyEntities": [], "tokensSaved": 120}

    async def erase_user(self, user_id: str) -> dict[str, Any]:
        self.calls.append(("erase_user", {"user_id": user_id}))
        return {"collections_purged": ["ctx_a", "ctx_users"], "collections_failed": []}


class FakeQdrant:
    def __init__(self, alive: bool = True, explode: bool = False) -> None:
        self.alive = alive
        self.explode = explode
        self.pings = 0

    async def ping(self) -> bool:
        self.pings += 1
        if self.explode:
            raise RuntimeError("qdrant is gone")
        return self.alive


def client(synatyx: Any = None, qdrant: Any = None) -> TestClient:
    app = Starlette(routes=list(routes))
    app.state.synatyx = synatyx
    app.state.qdrant = qdrant
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_health_cache():
    """The health cache is module state; a stale entry would leak between tests."""
    v1._health_cache = None
    yield
    v1._health_cache = None


# ---------------------------------------------------------------------------
# Request validation and the error envelope
# ---------------------------------------------------------------------------

def test_missing_fields_are_all_named_at_once():
    response = client(FakeSynatyx()).post("/v1/ingest", json={})

    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "INVALID_REQUEST"
    assert set(body["details"]["missing"]) == {"project", "sourceId"}


def test_invalid_json_is_a_400_not_a_500():
    response = client(FakeSynatyx()).post(
        "/v1/retrieve", content=b"{not json", headers={"content-type": "application/json"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_routes_answer_503_before_the_server_is_ready():
    response = client(None).post("/v1/retrieve", json={"project": "cx-a", "query": "x"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "NOT_READY"


def test_snake_case_field_names_are_accepted():
    fake = FakeSynatyx()
    response = client(fake).post(
        "/v1/ingest", json={"project": "cx-a", "source_id": "src-9", "text": "hello"}
    )

    assert response.status_code == 200
    assert fake.calls[0][1]["source_id"] == "src-9"


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def test_ingest_requires_exactly_one_of_url_or_text():
    fake = FakeSynatyx()
    both = client(fake).post(
        "/v1/ingest",
        json={"project": "cx-a", "sourceId": "s", "url": "https://x.test", "text": "hi"},
    )
    neither = client(fake).post("/v1/ingest", json={"project": "cx-a", "sourceId": "s"})

    assert both.status_code == 400
    assert neither.status_code == 400
    assert both.json()["error"]["code"] == "INVALID_REQUEST"


def test_ingest_passes_source_and_metadata_through_and_answers_in_camel_case():
    fake = FakeSynatyx()
    response = client(fake).post(
        "/v1/ingest",
        json={
            "project": "cx-lens-ai",
            "sourceId": "help-42",
            "url": "https://help.example.com/a",
            "metadata": {"title": "Refunds", "locale": "en"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "sourceId": "src-1",
        "source": "https://help.example.com/a",
        "chunkCount": 3,
        "chunksFailed": 0,
    }
    _, kwargs = fake.calls[0]
    assert kwargs["project"] == "cx-lens-ai"
    assert kwargs["source_id"] == "help-42"
    assert kwargs["metadata"] == {"title": "Refunds", "locale": "en"}


def test_ingest_rejects_non_object_metadata():
    response = client(FakeSynatyx()).post(
        "/v1/ingest",
        json={"project": "cx-a", "sourceId": "s", "text": "hi", "metadata": "nope"},
    )

    assert response.status_code == 400


def test_ingest_failure_is_a_502_with_the_upstream_code():
    fake = FakeSynatyx(raises=RuntimeError("openai down"))
    response = client(fake).post(
        "/v1/ingest", json={"project": "cx-a", "sourceId": "s", "text": "hi"}
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------

def _item(**overrides: Any) -> dict[str, Any]:
    item = {
        "id": "chunk-1",
        "content": "You can cancel from Settings.",
        "score": 0.81,
        "metadata": {
            "source_id": "help-42",
            "url": "https://help.example.com/cancel",
            "title": "Cancelling",
            "offset_start": 0,
            "offset_end": 29,
        },
    }
    item.update(overrides)
    return item


def test_retrieve_maps_items_to_citable_chunks():
    fake = FakeSynatyx(tool_result={"context_items": [_item()]})
    response = client(fake).post(
        "/v1/retrieve", json={"project": "cx-a", "query": "how do I cancel", "topK": 3}
    )

    assert response.status_code == 200
    assert response.json()["chunks"] == [
        {
            "chunkId": "chunk-1",
            "sourceId": "help-42",
            "text": "You can cancel from Settings.",
            "score": 0.81,
            "url": "https://help.example.com/cancel",
            "title": "Cancelling",
            "offsets": {"start": 0, "end": 29},
        }
    ]
    name, args = fake.calls[0]
    assert name == "context_retrieve"
    assert args["top_k"] == 3
    assert args["project"] == "cx-a"


def test_retrieve_sends_null_citation_fields_rather_than_omitting_them():
    fake = FakeSynatyx(tool_result={"context_items": [_item(metadata={"source_id": "s"})]})
    chunk = client(fake).post(
        "/v1/retrieve", json={"project": "cx-a", "query": "q"}
    ).json()["chunks"][0]

    assert chunk["url"] is None
    assert chunk["title"] is None
    assert chunk["offsets"] is None


def test_retrieve_does_not_offer_a_file_path_as_a_citation_link():
    fake = FakeSynatyx(
        tool_result={"context_items": [_item(metadata={"source": "/tmp/help.md"})]}
    )
    chunk = client(fake).post(
        "/v1/retrieve", json={"project": "cx-a", "query": "q"}
    ).json()["chunks"][0]

    assert chunk["url"] is None


def test_retrieve_falls_back_to_the_section_heading_for_a_title():
    fake = FakeSynatyx(
        tool_result={"context_items": [_item(metadata={"section": "Refunds"})]}
    )
    chunk = client(fake).post(
        "/v1/retrieve", json={"project": "cx-a", "query": "q"}
    ).json()["chunks"][0]

    assert chunk["title"] == "Refunds"


def test_empty_knowledge_base_is_a_200_with_diagnostics_not_an_error():
    fake = FakeSynatyx(
        tool_result={"context_items": [], "diagnostics": {"hint": "nothing stored yet"}}
    )
    response = client(fake).post("/v1/retrieve", json={"project": "cx-a", "query": "q"})

    assert response.status_code == 200
    assert response.json()["chunks"] == []
    assert response.json()["diagnostics"]["hint"] == "nothing stored yet"


def test_retrieve_defaults_top_k():
    fake = FakeSynatyx()
    client(fake).post("/v1/retrieve", json={"project": "cx-a", "query": "q"})

    assert fake.calls[0][1]["top_k"] == v1.DEFAULT_TOP_K


@pytest.mark.parametrize("top_k", ["many", 0, -1])
def test_retrieve_rejects_a_bad_top_k(top_k: Any):
    response = client(FakeSynatyx()).post(
        "/v1/retrieve", json={"project": "cx-a", "query": "q", "topK": top_k}
    )

    assert response.status_code == 400


def test_a_failing_tool_becomes_a_502():
    fake = FakeSynatyx(tool_result={"error": "qdrant unreachable", "tool": "context_retrieve"})
    response = client(fake).post("/v1/retrieve", json={"project": "cx-a", "query": "q"})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Sources and memory
# ---------------------------------------------------------------------------

def test_deprecating_a_source_returns_a_count():
    fake = FakeSynatyx()
    response = client(fake).post(
        "/v1/sources/deprecate", json={"project": "cx-a", "sourceId": "help-42"}
    )

    assert response.status_code == 200
    assert response.json() == {"sourceId": "help-42", "deprecated": 4}


def test_memory_store_returns_the_new_item_id():
    fake = FakeSynatyx(tool_result={"item_id": "m-1", "item_ids": ["m-1"]})
    response = client(fake).post(
        "/v1/memory/store",
        json={"project": "cx-a", "userId": "end-user-9", "content": "asked about billing"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == "m-1"
    name, args = fake.calls[0]
    assert name == "context_store"
    assert args["user_id"] == "end-user-9"
    assert args["memory_layer"] == "L2"


def test_memory_retrieve_returns_items_with_scores():
    fake = FakeSynatyx(tool_result={"context_items": [_item()]})
    response = client(fake).post(
        "/v1/memory/retrieve",
        json={"project": "cx-a", "userId": "end-user-9", "query": "billing"},
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == "chunk-1"
    assert response.json()["items"][0]["score"] == 0.81


def test_memory_summarize_returns_the_summary_itself():
    fake = FakeSynatyx()
    response = client(fake).post(
        "/v1/memory/summarize",
        json={"project": "cx-a", "userId": "end-user-9", "conversationId": "conv-1"},
    )

    assert response.status_code == 200
    assert response.json()["summary"] == "they asked about billing"
    assert response.json()["tokensSaved"] == 120
    assert fake.calls[0][1]["session_id"] == "conv-1"


# ---------------------------------------------------------------------------
# Erasure
# ---------------------------------------------------------------------------

def test_erasing_a_user_reports_which_collections_were_purged():
    fake = FakeSynatyx()
    response = client(fake).delete("/v1/users/end-user-9")

    assert response.status_code == 200
    assert response.json() == {
        "userId": "end-user-9",
        "collectionsPurged": ["ctx_a", "ctx_users"],
    }


def test_a_partial_erasure_is_not_reported_as_success():
    class PartialFailure(FakeSynatyx):
        async def erase_user(self, user_id: str) -> dict[str, Any]:
            return {"collections_purged": ["ctx_a"], "collections_failed": ["ctx_b"]}

    response = client(PartialFailure()).delete("/v1/users/end-user-9")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.json()["error"]["details"]["collectionsFailed"] == ["ctx_b"]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health_is_ok_when_the_vector_store_answers():
    response = client(FakeSynatyx(), FakeQdrant(alive=True)).get("/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "vectorStore": "ok"}


def test_health_is_503_when_the_vector_store_is_unreachable():
    response = client(FakeSynatyx(), FakeQdrant(explode=True)).get("/v1/health")

    assert response.status_code == 503
    assert response.json()["vectorStore"] == "unreachable"


def test_health_caches_so_probes_do_not_hammer_the_vector_store():
    qdrant = FakeQdrant(alive=True)
    probe = client(FakeSynatyx(), qdrant)

    for _ in range(5):
        assert probe.get("/v1/health").status_code == 200

    assert qdrant.pings == 1


# ---------------------------------------------------------------------------
# A client's mistake must never look like an outage
# ---------------------------------------------------------------------------

def test_a_caller_error_from_a_tool_is_a_400_not_a_502():
    # The consumer treats every 5xx as "Synatyx is down" and pages someone, so
    # a mistyped field must not arrive as one.
    fake = FakeSynatyx(tool_result={
        "error": "'NOPE' is not a valid MemoryLayer",
        "tool": "context_store",
        "error_type": "ValueError",
    })
    response = client(fake).post(
        "/v1/memory/store",
        json={"project": "cx-a", "userId": "u", "content": "hi", "memoryLayer": "NOPE"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_a_backend_failure_is_still_a_502():
    fake = FakeSynatyx(tool_result={
        "error": "connection refused",
        "tool": "context_retrieve",
        "error_type": "ConnectionError",
    })
    response = client(fake).post("/v1/retrieve", json={"project": "cx-a", "query": "q"})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"


def test_an_unhandled_exception_still_answers_in_an_envelope():
    # Without this the route would return HTML or bare text, which a consumer
    # cannot tell apart from a crashing proxy in front of the server.
    fake = FakeSynatyx(raises=RuntimeError("boom"))
    response = client(fake).post(
        "/v1/memory/retrieve", json={"project": "cx-a", "userId": "u", "query": "q"}
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"


def test_retrieve_pushes_filters_down_as_stored_metadata_keys():
    fake = FakeSynatyx()
    client(fake).post(
        "/v1/retrieve",
        json={"project": "cx-a", "query": "q", "filters": {"locale": "en", "sourceId": "help-42"}},
    )

    # sourceId is what the API speaks; source_id is what the payload stores.
    # Without the rename the filter matches nothing and the caller just sees
    # an empty result.
    assert fake.calls[0][1]["filters"] == {"locale": "en", "source_id": "help-42"}


def test_retrieve_rejects_non_object_filters():
    response = client(FakeSynatyx()).post(
        "/v1/retrieve", json={"project": "cx-a", "query": "q", "filters": "locale=en"}
    )

    assert response.status_code == 400
