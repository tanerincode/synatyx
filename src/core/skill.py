from __future__ import annotations

from typing import Any

from src.core.embedder import get_embedder
from src.models.memory_layer import MemoryLayer
from src.models.skill import Skill
from src.storage.postgres import PostgresStorage
from src.storage.qdrant import QdrantStorage


class SkillService:
    def __init__(self, qdrant: QdrantStorage, postgres: PostgresStorage) -> None:
        self._qdrant = qdrant
        self._postgres = postgres
        self._embedder = get_embedder()

    async def store(
        self,
        name: str,
        description: str,
        content: str,
        user_id: str,
        project: str | None = None,
        frontmatter: dict[str, Any] | None = None,
    ) -> Skill:
        """Save a skill to PG and embed its description into Qdrant L3."""
        skill = Skill(
            name=name,
            description=description,
            content=content,
            user_id=user_id,
            project=project,
            frontmatter=frontmatter or {},
        )
        skill = await self._postgres.skill_store(skill)
        embed_text = f"{skill.name}: {skill.description}"
        vector = await self._embedder.embed(embed_text)
        await self._qdrant.skill_upsert(
            skill_id=skill.id,
            name=skill.name,
            slug=skill.slug,
            vector=vector,
            user_id=user_id,
            project=project,
        )
        return skill

    async def find(
        self,
        query: str,
        user_id: str,
        project: str | None = None,
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """RAG search: embed query → Qdrant L3 (type=skill) → fetch full content from PG."""
        vector = await self._embedder.embed(query)
        hits = await self._qdrant.search(
            query_vector=vector,
            user_id=user_id,
            top_k=top_k,
            memory_layer=MemoryLayer.L3,
            type_filter="skill",
            project=project,
        )
        results: list[dict[str, Any]] = []
        for hit in hits:
            skill_id = hit.metadata.get("skill_id")
            if not skill_id:
                continue
            skill = await self._postgres.skill_get_by_id(skill_id, user_id)
            if skill:
                results.append({
                    "name": skill.name,
                    "description": skill.description,
                    "content": skill.content,
                    "frontmatter": skill.frontmatter,
                    "score": hit.score,
                })
        return results

    async def get(
        self,
        name: str,
        user_id: str,
        project: str | None = None,
    ) -> Skill | None:
        """Fetch a skill by exact name or slug from PG."""
        return await self._postgres.skill_get_by_name(name, user_id, project)

    async def list_skills(
        self,
        user_id: str,
        project: str | None = None,
        limit: int = 50,
    ) -> list[Skill]:
        """List all skills for a user, optionally scoped to a project."""
        return await self._postgres.skill_list(user_id, project, limit)

    async def delete(self, name: str, user_id: str) -> bool:
        """Delete skill from PG and deprecate its Qdrant embedding."""
        skill = await self._postgres.skill_get_by_name(name, user_id)
        if not skill:
            return False
        deleted = await self._postgres.skill_delete(skill.id, user_id)
        if deleted:
            await self._qdrant.deprecate(skill.id, reason="skill deleted")
        return deleted

