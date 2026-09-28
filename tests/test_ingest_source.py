from __future__ import annotations

from typing import Any

import pytest

from src.core.ingest import IngestResult
from src.transports.mcp.server import SynatyxMCPServer


class RecordingIngest:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def ingest(self, **kwargs: Any) -> IngestResult:
        self.calls.append(("ingest", kwargs))
        return IngestResult(
            source=kwargs["source"], chunks_stored=2, chunks_failed=0, total_chunks=2
        )

    async def ingest_text(self, **kwargs: Any) -> IngestResult:
        self.calls.append(("ingest_text", kwargs))
        return IngestResult(
            source=kwargs["source"], chunks_stored=1, chunks_failed=0, total_chunks=1
        )


def _server(ingest: RecordingIngest) -> SynatyxMCPServer:
    """A server whose only wired part is the ingest service under test."""
    server = SynatyxMCPServer.__new__(SynatyxMCPServer)

    async def _get_services(user_id: str, project: str | None = None):
        return None, None, None, ingest, None

    server._get_services = _get_services  # type: ignore[method-assign]
    return server


async def test_url_ingest_tags_every_chunk_with_the_source_id():
    ingest = RecordingIngest()
    result = await _server(ingest).ingest_source(
        user_id="cx-service",
        project="cx-lens-ai",
        source_id="help-42",
        url="https://help.example.com/cancel",
        metadata={"title": "Cancelling"},
    )

    name, kwargs = ingest.calls[0]
    assert name == "ingest"
    assert kwargs["metadata"] == {"title": "Cancelling", "source_id": "help-42"}
    assert result["chunks_stored"] == 2


async def test_pushed_text_falls_back_to_the_source_id_when_no_url_is_given():
    ingest = RecordingIngest()
    await _server(ingest).ingest_source(
        user_id="cx-service", project="cx-a", source_id="upload-7", text="hello"
    )

    name, kwargs = ingest.calls[0]
    assert name == "ingest_text"
    # Not the string "None": a missing url must not become the recorded source.
    assert kwargs["source"] == "upload-7"


async def test_pushed_text_prefers_a_url_from_the_metadata():
    ingest = RecordingIngest()
    await _server(ingest).ingest_source(
        user_id="cx-service",
        project="cx-a",
        source_id="upload-7",
        text="hello",
        metadata={"url": "https://help.example.com/a"},
    )

    assert ingest.calls[0][1]["source"] == "https://help.example.com/a"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"url": "https://x.test", "text": "hi"},
        {},
    ],
)
async def test_exactly_one_of_url_or_text_is_required(kwargs: dict[str, Any]):
    with pytest.raises(ValueError):
        await _server(RecordingIngest()).ingest_source(
            user_id="cx-service", project="cx-a", source_id="s", **kwargs
        )
