from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class SessionStatus(str, Enum):
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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    summarized_at: datetime | None = None
    metadata: dict = Field(default_factory=dict)

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

    def mark_summarized(self, summary: str, key_entities: list[KeyEntity]) -> None:
        self.summary = summary
        self.key_entities = key_entities
        self.status = SessionStatus.SUMMARIZED
        self.summarized_at = datetime.now(timezone.utc)
        self.touch()

    model_config = {"frozen": False}

