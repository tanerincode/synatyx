from __future__ import annotations

from typing import Any

# Metadata keys an ingested chunk carries so it can be cited later. Written by
# IngestService; read by every transport.
SOURCE_ID = "source_id"
OFFSET_START = "offset_start"
OFFSET_END = "offset_end"


def citation_url(metadata: dict[str, Any]) -> str | None:
    """A URL a caller can link to, or nothing.

    `source` holds a URL for crawled documents and a file path or an opaque
    caller identifier for everything else. Handing back a path as a citation
    link puts a dead link in someone's UI, so only real URLs come back.
    """
    for key in ("url", "source"):
        value = metadata.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return None


def offsets(metadata: dict[str, Any]) -> dict[str, int] | None:
    """Where this chunk sits in the document it came from, when known."""
    start = metadata.get(OFFSET_START)
    end = metadata.get(OFFSET_END)
    if isinstance(start, int) and isinstance(end, int):
        return {"start": start, "end": end}
    return None


def citation(metadata: dict[str, Any]) -> dict[str, Any]:
    """The fields an answer needs to attribute a chunk.

    Always the same keys, explicitly null when unknown, rather than a dict
    whose shape depends on what happened to be ingested. A caller rendering
    citations needs to tell "no title" apart from "this build does not send
    titles", and only one of those is worth a bug report.

    Shared by both transports on purpose: the REST response and the MCP tool
    result are built from this one function, so a chunk cannot be citable over
    one and anonymous over the other.
    """
    return {
        "sourceId": metadata.get(SOURCE_ID),
        "url": citation_url(metadata),
        "title": metadata.get("title") or metadata.get("section"),
        "offsets": offsets(metadata),
    }


def annotate(item: dict[str, Any]) -> dict[str, Any]:
    """Add citation fields to a dumped context item, in place."""
    item["citation"] = citation(item.get("metadata") or {})
    return item
