from __future__ import annotations

import builtins

from src.models.handoff import (
    OPEN_HANDOFF_STATUSES,
    Handoff,
    HandoffReport,
    HandoffStatus,
    HandoffVersion,
)
from src.models.skill import _slugify
from src.storage.postgres import PostgresStorage


class HandoffService:
    """Briefs between agents, stored in Postgres. Every call is scoped to user_id."""

    def __init__(self, storage: PostgresStorage) -> None:
        self._storage = storage

    async def create(
        self,
        user_id: str,
        project: str,
        title: str,
        body: str,
        *,
        assignee: str | None = None,
        slug: str | None = None,
        parent_id: str | None = None,
        status: HandoffStatus | None = None,
        task_ids: list[str] | None = None,
        created_by: str | None = None,
    ) -> Handoff:
        slug = _slugify(slug or title)
        if not slug:
            raise ValueError("handoff needs a title or slug with letters or digits")
        if parent_id is not None and not await self._storage.handoff_get(user_id, parent_id):
            raise ValueError(f"parent handoff '{parent_id}' not found")
        if await self._storage.handoff_get(user_id, project=project, slug=slug):
            raise ValueError(f"handoff slug '{slug}' already exists in project '{project}'")
        if status is None:
            status = HandoffStatus.ASSIGNED if assignee else HandoffStatus.DRAFT
        handoff = Handoff(
            user_id=user_id,
            project=project,
            slug=slug,
            title=title,
            assignee=assignee,
            status=status,
            body=body,
            parent_id=parent_id,
            task_ids=list(task_ids or []),
            created_by=created_by,
        )
        # storage raises ValueError too if a concurrent create wins the slug
        return await self._storage.handoff_create(handoff)

    async def get(
        self,
        user_id: str,
        handoff_id: str | None = None,
        *,
        project: str | None = None,
        slug: str | None = None,
        version: int | None = None,
    ) -> tuple[Handoff, list[HandoffReport]] | None:
        handoff = await self._storage.handoff_get(
            user_id, handoff_id, project=project, slug=slug
        )
        if handoff is None:
            return None
        if version is not None and version != handoff.version:
            row = await self._storage.handoff_get_version(handoff.id, version)
            if row is None:
                raise ValueError(f"version {version} of handoff '{handoff.id}' not found")
            handoff = handoff.model_copy(update={"body": row.body, "version": row.version})
        reports = await self._storage.handoff_reports(handoff.id, limit=10)
        return handoff, reports

    async def list(
        self,
        user_id: str,
        *,
        project: str | None = None,
        assignee: str | None = None,
        status: HandoffStatus | None = None,
        limit: int = 50,
    ) -> list[Handoff]:
        return await self._storage.handoff_list(
            user_id,
            project=project,
            assignee=assignee,
            statuses=[status] if status else None,
            limit=limit,
        )

    async def update(
        self,
        user_id: str,
        handoff_id: str,
        *,
        body: str | None = None,
        title: str | None = None,
        status: HandoffStatus | None = None,
        assignee: str | None = None,
        author: str | None = None,
    ) -> Handoff | None:
        handoff = await self._storage.handoff_get(user_id, handoff_id)
        if handoff is None:
            return None
        new_version: HandoffVersion | None = None
        if body is not None and body != handoff.body:
            handoff.version += 1
            handoff.body = body
            new_version = HandoffVersion(
                handoff_id=handoff.id, version=handoff.version, body=body, author=author
            )
        if title is not None:
            handoff.title = title
        if status is not None:
            handoff.status = status
        if assignee is not None:
            handoff.assignee = assignee
        handoff.touch()
        return await self._storage.handoff_save(handoff, new_version)

    async def report(
        self,
        user_id: str,
        handoff_id: str,
        agent: str,
        text: str,
        *,
        status: HandoffStatus | None = None,
    ) -> HandoffReport | None:
        if await self._storage.handoff_get(user_id, handoff_id) is None:
            return None
        report = HandoffReport(
            handoff_id=handoff_id, agent=agent, text=text, status_after=status
        )
        return await self._storage.handoff_add_report(report, status)

    async def mine(
        self, user_id: str, assignee: str, *, project: str | None = None
    ) -> builtins.list[Handoff]:
        return await self._storage.handoff_list(
            user_id,
            project=project,
            assignee=assignee,
            statuses=list(OPEN_HANDOFF_STATUSES),
        )
