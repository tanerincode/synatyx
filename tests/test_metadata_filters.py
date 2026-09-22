from __future__ import annotations

from typing import Any

from src.models.memory_layer import MemoryLayer
from src.storage.qdrant import QdrantStorage
from src.transports.mcp.tools import TOOL_DEFINITIONS


class RecordingClient:
    """Captures the filter a search was issued with."""

    def __init__(self) -> None:
        self.query_filter: Any = None

    async def query_points(self, **kwargs: Any) -> Any:
        self.query_filter = kwargs["query_filter"]

        class _Empty:
            points: list[Any] = []

        return _Empty()


def _storage() -> tuple[QdrantStorage, RecordingClient]:
    storage = QdrantStorage(host="localhost", port=6333, collection_name="ctx_test")
    client = RecordingClient()
    storage._client = client  # type: ignore[assignment]
    return storage, client


def _conditions(client: RecordingClient) -> dict[str, Any]:
    return {c.key: c.match.value for c in client.query_filter.must}


async def test_metadata_filters_become_nested_payload_conditions():
    storage, client = _storage()

    await storage.search(
        query_vector=[0.0] * 8,
        user_id="cx-service",
        metadata_filters={"locale": "en", "source_id": "help-42"},
    )

    conditions = _conditions(client)
    # Metadata is a nested object on the point, so the keys are dotted.
    assert conditions["metadata.locale"] == "en"
    assert conditions["metadata.source_id"] == "help-42"


async def test_filters_are_applied_by_the_store_not_after_the_fact():
    # The whole point of pushing them down: a filtered search still returns up
    # to top_k items. Trimming afterwards would return whatever survived.
    storage, client = _storage()

    await storage.search(
        query_vector=[0.0] * 8,
        user_id="u",
        top_k=8,
        memory_layer=MemoryLayer.L3,
        metadata_filters={"locale": "tr"},
    )

    assert "metadata.locale" in _conditions(client)


async def test_no_filters_leaves_the_query_untouched():
    storage, client = _storage()

    await storage.search(query_vector=[0.0] * 8, user_id="u")

    assert not any(key.startswith("metadata.") for key in _conditions(client))


def test_the_tools_cx_depends_on_exist_with_the_agreed_arguments():
    """Pins the tool contract the CX service builds its client against.

    These names and argument names are agreed with the consumer; renaming one
    silently is a broken integration rather than a refactor.
    """
    by_name = {t["name"]: t for t in TOOL_DEFINITIONS}

    ingest = by_name["context_ingest"]["parameters"]["properties"]
    assert {"source", "text", "source_id", "url", "title", "locale"} <= set(ingest)

    assert "filters" in by_name["context_retrieve"]["parameters"]["properties"]
    assert "sync" in by_name["context_summarize"]["parameters"]["properties"]

    deprecate = by_name["context_deprecate_source"]["parameters"]
    assert deprecate["required"] == ["source_id", "user_id"]

    # Erasing someone must be deliberate: the target is its own argument, never
    # the caller's own user_id arriving by default.
    erase = by_name["context_erase_user"]["parameters"]
    assert "target_user_id" in erase["properties"]
    assert "target_user_id" in erase["required"]
