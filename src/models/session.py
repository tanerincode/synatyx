from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SessionStatus(str, Enum):  # noqa: UP042  # StrEnum changes str() output
    ACTIVE = "active"
    SUMMARIZED = "summarized"
    CLOSED = "closed"


class KeyEntity(BaseModel):
    """An important entity extracted from a session."""

    name: str
    type: str  # e.g. "project", "language", "preference", "decision"
    value: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class Session(BaseModel):
    """Represents a conversation session in the Synatyx Context Engine."""

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    status: SessionStatus = SessionStatus.ACTIVE
    summary: str | None = None
    key_entities: list[KeyEntity] = Field(default_factory=list)
    token_count: int = 0
    message_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    summarized_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC)

    def mark_summarized(self, summary: str, key_entities: list[KeyEntity]) -> None:
        self.summary = summary
        self.key_entities = key_entities
        self.status = SessionStatus.SUMMARIZED
        self.summarized_at = datetime.now(UTC)
        self.touch()

    model_config = {"frozen": False}

