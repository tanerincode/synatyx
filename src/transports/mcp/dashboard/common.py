from __future__ import annotations

from datetime import datetime
from typing import Any

from starlette.requests import Request

# Collections that hold memories. ctx_users is the shared L4 collection; every
# other ctx_* collection is one project.
_COLLECTION_PREFIX = "ctx_"


def _is_memory_collection(name: str) -> bool:
    """Memory collections only — code/doc index collections (ctx_<slug>__index)
    are not projects and would show up as phantom entries."""
    from src.core.project import is_index_collection
    return name.startswith(_COLLECTION_PREFIX) and not is_index_collection(name)


def _storages(request: Request) -> tuple[Any, Any] | None:
    qdrant = getattr(request.app.state, "qdrant", None)
    postgres = getattr(request.app.state, "postgres", None)
    if qdrant is None or postgres is None:
        return None
    return qdrant, postgres


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None
