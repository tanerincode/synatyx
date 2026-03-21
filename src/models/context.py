from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator

from src.models.memory_layer import MemoryLayer


class ContextItem(BaseModel):
    """A single unit of memory in the Synatyx Context Engine."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    session_id: str | None = None
    content: str
    memory_layer: MemoryLayer
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    embedding: list[float] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_pinned: bool = False
    is_deprecated: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("content")
    @classmethod
    def content_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content must not be empty")
        return v.strip()

    @property
    def token_estimate(self) -> int:
        """Rough token estimate: ~4 chars per token."""
        return len(self.content) // 4

    def deprecate(self, reason: str | None = None) -> None:
        self.is_deprecated = True
        if reason:
            self.metadata["deprecated_reason"] = reason
        self.updated_at = datetime.now(timezone.utc)

    model_config = {"frozen": False}


class ScoredContextItem(ContextItem):
    """ContextItem with a combined relevance score attached after scoring."""

    recency_score: float = 0.0
    semantic_score: float = 0.0
    importance_score: float = 0.0
    user_signal_score: float = 0.0

