from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.config import settings
from src.models.session import KeyEntity, Session, SessionStatus


# ---------------------------------------------------------------------------
# ORM Base & Table Models
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_entities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    summarized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserProfileRow(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    profile: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AuditLogRow(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Storage Client
# ---------------------------------------------------------------------------

class PostgresStorage:
    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or settings.postgres.dsn
        self._engine = create_async_engine(
            self._dsn,
            echo=settings.debug,
            pool_pre_ping=True,      # test connection before use — kills stale connections
            pool_recycle=1800,       # recycle connections older than 30 min
        )
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def connect(self) -> None:
        """Verify the DB connection is reachable.
        Schema is managed by Alembic — run `alembic upgrade head` to apply migrations.
        """
        async with self._engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))

    # -------------------------------------------------------------------------
    # Session CRUD
    # -------------------------------------------------------------------------

    async def session_create(self, session: Session) -> None:
        async with self._session_factory() as db:
            db.add(SessionRow(
                session_id=session.session_id,
                user_id=session.user_id,
                status=session.status.value,
                summary=session.summary,
                key_entities=[e.model_dump() for e in session.key_entities],
                token_count=session.token_count,
                message_count=session.message_count,
                metadata_=session.metadata,
                created_at=session.created_at,
                updated_at=session.updated_at,
                summarized_at=session.summarized_at,
            ))
            await db.commit()

    async def session_get(self, session_id: str) -> Session | None:
        async with self._session_factory() as db:
            row = await db.get(SessionRow, session_id)
        return self._row_to_session(row) if row else None

    async def session_update(self, session: Session) -> None:
        async with self._session_factory() as db:
            row = await db.get(SessionRow, session.session_id)
            if not row:
                return
            row.status = session.status.value
            row.summary = session.summary
            row.key_entities = [e.model_dump() for e in session.key_entities]
            row.token_count = session.token_count
            row.message_count = session.message_count
            row.metadata_ = session.metadata
            row.updated_at = datetime.now(timezone.utc)
            row.summarized_at = session.summarized_at
            await db.commit()

    async def session_delete(self, session_id: str) -> None:
        async with self._session_factory() as db:
            row = await db.get(SessionRow, session_id)
            if row:
                await db.delete(row)
                await db.commit()

    # -------------------------------------------------------------------------
    # User Profile CRUD
    # -------------------------------------------------------------------------

    async def profile_upsert(self, user_id: str, profile: dict[str, Any]) -> None:
        async with self._session_factory() as db:
            row = await db.get(UserProfileRow, user_id)
            if row:
                row.profile = profile
                row.updated_at = datetime.now(timezone.utc)
            else:
                db.add(UserProfileRow(user_id=user_id, profile=profile))
            await db.commit()

    async def profile_get(self, user_id: str) -> dict[str, Any]:
        async with self._session_factory() as db:
            row = await db.get(UserProfileRow, user_id)
        return row.profile if row else {}

    # -------------------------------------------------------------------------
    # Audit Log
    # -------------------------------------------------------------------------

    async def audit(self, user_id: str, action: str, payload: dict[str, Any] | None = None) -> None:
        async with self._session_factory() as db:
            db.add(AuditLogRow(user_id=user_id, action=action, payload=payload or {}))
            await db.commit()

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _row_to_session(self, row: SessionRow) -> Session:
        return Session(
            session_id=row.session_id,
            user_id=row.user_id,
            status=SessionStatus(row.status),
            summary=row.summary,
            key_entities=[KeyEntity(**e) for e in row.key_entities],
            token_count=row.token_count,
            message_count=row.message_count,
            metadata=row.metadata_,
            created_at=row.created_at,
            updated_at=row.updated_at,
            summarized_at=row.summarized_at,
        )

    async def close(self) -> None:
        await self._engine.dispose()

