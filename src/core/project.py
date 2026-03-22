from __future__ import annotations

import re
from pathlib import Path

from src.storage.qdrant import QdrantStorage
from src.storage.redis import RedisStorage

COLLECTION_PREFIX = "ctx_"

# L4 (procedural / user preferences) lives in one shared collection — it's about
# the user, not the codebase, so it must not be fragmented across projects.
L4_COLLECTION_SLUG = "users"


def slugify(name: str) -> str:
    """Convert a project name to a safe Qdrant collection slug.

    Examples:
        "taty-v2"   → "taty_v2"
        "My Project" → "my_project"
        "synatyx"   → "synatyx"
    """
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")
    return slug or "default"


def collection_for(slug: str) -> str:
    """Return the Qdrant collection name for a given project slug."""
    return f"{COLLECTION_PREFIX}{slug}"


class ProjectManager:
    """Manages per-project Qdrant collections, backed by Redis for state persistence.

    - Project slugs are stored in Redis so the active project survives MCP server restarts.
    - QdrantStorage instances are cached in-process per slug (one client per collection).
    - When no project is set, falls back to the default storage and surfaces a CWD-based suggestion.
    """

    def __init__(self, redis: RedisStorage, default_storage: QdrantStorage) -> None:
        self._redis = redis
        self._default = default_storage
        self._cache: dict[str, QdrantStorage] = {}

    async def set_project(self, user_id: str, project_name: str) -> tuple[str, QdrantStorage]:
        """Set the active project for a user. Creates the collection if needed.

        Returns:
            (slug, storage) — the canonical slug and the ready QdrantStorage.
        """
        slug = slugify(project_name)
        storage = await self._ensure_storage(slug)
        await self._redis.project_set(user_id, slug)
        return slug, storage

    async def get_project(self, user_id: str) -> str | None:
        """Return the active project slug for a user, or None if not set."""
        return await self._redis.project_get(user_id)

    async def get_storage(
        self, user_id: str
    ) -> tuple[QdrantStorage, str | None]:
        """Return the QdrantStorage for the user's active project.

        Returns:
            (storage, cwd_suggestion) — if no project is set, storage is the
            default and cwd_suggestion is the detected workspace folder name
            so the caller can prompt the user to confirm.
        """
        slug = await self._redis.project_get(user_id)
        if not slug:
            suggestion = _detect_cwd_name()
            return self._default, suggestion
        storage = await self._ensure_storage(slug)
        return storage, None

    async def get_l4_storage(self) -> QdrantStorage:
        """Return the single shared QdrantStorage for L4 (user-global preferences).

        L4 is about the user, not the project — always routed to ctx_users regardless
        of which project is currently active.
        """
        return await self._ensure_storage(L4_COLLECTION_SLUG)

    async def _ensure_storage(self, slug: str) -> QdrantStorage:
        """Return a cached (or newly created) QdrantStorage for the given slug."""
        if slug not in self._cache:
            storage = QdrantStorage(
                host=self._default.host,
                port=self._default.port,
                collection_name=collection_for(slug),
            )
            await storage.init_collection()
            self._cache[slug] = storage
        return self._cache[slug]


def _detect_cwd_name() -> str:
    """Return the name of the current working directory."""
    return Path.cwd().name

