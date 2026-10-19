from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class HandoffStatus(str, Enum):  # noqa: UP042
    DRAFT = "draft"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


# What "mine" and context_brief show: handoffs someone still has to act on.
OPEN_HANDOFF_STATUSES = (
    HandoffStatus.ASSIGNED,
    HandoffStatus.IN_PROGRESS,
    HandoffStatus.BLOCKED,
)


def _now() -> datetime:
    return datetime.now(UTC)


class Handoff(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    project: str
    slug: str
    title: str
    assignee: str | None = None
    status: HandoffStatus = HandoffStatus.DRAFT
    body: str = ""
    version: int = 1
    parent_id: str | None = None
    task_ids: list[str] = Field(default_factory=list)
    created_by: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    def touch(self) -> None:
        self.updated_at = _now()

    model_config = {"frozen": False}


class HandoffVersion(BaseModel):
    handoff_id: str
    version: int
    body: str
    author: str | None = None
    created_at: datetime = Field(default_factory=_now)


class HandoffReport(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    handoff_id: str
    agent: str
    text: str
    status_after: HandoffStatus | None = None
    created_at: datetime = Field(default_factory=_now)
