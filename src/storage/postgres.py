from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    delete,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.config import settings
from src.models.relation import MemoryRelation
from src.models.session import KeyEntity, Session, SessionStatus
from src.models.skill import Skill, _slugify
from src.models.task import Task, TaskPriority, TaskStatus


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


class SkillRow(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    frontmatter: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    project: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GCLogRow(Base):
    __tablename__ = "gc_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(String, nullable=False)
    collection: Mapped[str] = mapped_column(String, nullable=False)
    memory_layer: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False, index=True)  # "deprecated" | "deleted"
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskRow(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending", index=True)
    priority: Mapped[str] = mapped_column(String, nullable=False, default="medium")
    project: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ToolUsageRow(Base):
    __tablename__ = "tool_usage"
    __table_args__ = (
        Index("ix_tool_usage_user_created", "user_id", "created_at"),
        Index("ix_tool_usage_project_created", "project", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    project: Mapped[str | None] = mapped_column(String, nullable=True)
    tool: Mapped[str] = mapped_column(String, nullable=False, index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class MemoryRelationRow(Base):
    __tablename__ = "memory_relations"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "source_item_id", "target_item_id", "relation_type",
            name="uq_memory_relations_edge",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source_item_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    target_item_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    relation_type: Mapped[str] = mapped_column(String, nullable=False, default="related_to")
    project: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
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

    # ── Task CRUD ────────────────────────────────────────────────────────────

    async def task_add(self, task: Task) -> Task:
        async with self._session_factory() as session:
            row = TaskRow(
                id=task.id,
                user_id=task.user_id,
                title=task.title,
                description=task.description,
                status=task.status.value,
                priority=task.priority.value,
                project=task.project,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return self._row_to_task(row)

    async def task_list(
        self,
        user_id: str,
        status: TaskStatus | None = None,
        priority: TaskPriority | None = None,
        project: str | None = None,
        limit: int = 50,
    ) -> list[Task]:
        async with self._session_factory() as session:
            stmt = select(TaskRow).where(TaskRow.user_id == user_id)
            if status:
                stmt = stmt.where(TaskRow.status == status.value)
            if priority:
                stmt = stmt.where(TaskRow.priority == priority.value)
            if project:
                stmt = stmt.where(TaskRow.project == project)
            stmt = stmt.order_by(TaskRow.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            return [self._row_to_task(r) for r in result.scalars().all()]

    async def task_list_all(
        self,
        status: TaskStatus | None = None,
        limit: int = 50,
    ) -> list[Task]:
        """List tasks across all users — dashboard use only."""
        async with self._session_factory() as session:
            stmt = select(TaskRow)
            if status:
                stmt = stmt.where(TaskRow.status == status.value)
            stmt = stmt.order_by(TaskRow.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            return [self._row_to_task(r) for r in result.scalars().all()]

    async def task_update(
        self,
        task_id: str,
        user_id: str,
        status: TaskStatus | None = None,
        priority: TaskPriority | None = None,
        title: str | None = None,
        description: str | None = None,
    ) -> Task | None:
        async with self._session_factory() as session:
            row = await session.get(TaskRow, task_id)
            if not row or row.user_id != user_id:
                return None
            if status:
                row.status = status.value
            if priority:
                row.priority = priority.value
            if title:
                row.title = title
            if description is not None:
                row.description = description
            await session.commit()
            await session.refresh(row)
            return self._row_to_task(row)

    @staticmethod
    def _row_to_task(row: TaskRow) -> Task:
        return Task(
            id=row.id,
            user_id=row.user_id,
            title=row.title,
            description=row.description,
            status=TaskStatus(row.status),
            priority=TaskPriority(row.priority),
            project=row.project,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    # ── GC Log ───────────────────────────────────────────────────────────────

    async def gc_log_add(
        self,
        run_id: str,
        item_id: str,
        collection: str,
        memory_layer: str,
        action: str,
        reason: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            session.add(GCLogRow(
                run_id=run_id,
                item_id=item_id,
                collection=collection,
                memory_layer=memory_layer,
                action=action,
                reason=reason,
            ))
            await session.commit()

    # ── Tool Usage ───────────────────────────────────────────────────────────

    async def usage_add(
        self,
        user_id: str,
        tool: str,
        input_tokens: int,
        output_tokens: int,
        embedding_tokens: int = 0,
        project: str | None = None,
        error: bool = False,
    ) -> None:
        async with self._session_factory() as session:
            session.add(ToolUsageRow(
                user_id=user_id,
                project=project,
                tool=tool,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                embedding_tokens=embedding_tokens,
                error=error,
            ))
            await session.commit()

    async def usage_totals(
        self,
        user_id: str | None = None,
        project: str | None = None,
        since: datetime | None = None,
    ) -> dict[str, int]:
        async with self._session_factory() as session:
            stmt = select(
                func.count(ToolUsageRow.id),
                func.coalesce(func.sum(ToolUsageRow.input_tokens), 0),
                func.coalesce(func.sum(ToolUsageRow.output_tokens), 0),
                func.coalesce(func.sum(ToolUsageRow.embedding_tokens), 0),
            )
            stmt = self._usage_filters(stmt, user_id, project, since)
            calls, inp, out, emb = (await session.execute(stmt)).one()
            return {
                "calls": calls,
                "input_tokens": int(inp),
                "output_tokens": int(out),
                "embedding_tokens": int(emb),
            }

    async def usage_stats(
        self,
        user_id: str | None = None,
        project: str | None = None,
        since: datetime | None = None,
        group_by: str = "tool",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Aggregated usage grouped by tool, project, or day."""
        if group_by == "project":
            key = func.coalesce(ToolUsageRow.project, "")
        elif group_by == "day":
            key = func.date_trunc("day", ToolUsageRow.created_at)
        else:
            key = ToolUsageRow.tool
        async with self._session_factory() as session:
            stmt = select(
                key.label("key"),
                func.count(ToolUsageRow.id),
                func.coalesce(func.sum(ToolUsageRow.input_tokens), 0),
                func.coalesce(func.sum(ToolUsageRow.output_tokens), 0),
                func.coalesce(func.sum(ToolUsageRow.embedding_tokens), 0),
            )
            stmt = self._usage_filters(stmt, user_id, project, since)
            stmt = stmt.group_by(key)
            # day series reads chronologically; tool/project ranked by spend
            if group_by == "day":
                stmt = stmt.order_by(key)
            else:
                stmt = stmt.order_by(func.sum(ToolUsageRow.output_tokens).desc())
            stmt = stmt.limit(limit)
            rows = (await session.execute(stmt)).all()
            return [
                {
                    group_by: k.isoformat()[:10] if isinstance(k, datetime) else k,
                    "calls": calls,
                    "input_tokens": int(inp),
                    "output_tokens": int(out),
                    "embedding_tokens": int(emb),
                }
                for k, calls, inp, out, emb in rows
            ]

    async def usage_prune(self, before: datetime) -> int:
        """Delete usage rows older than the cutoff. Returns rows removed."""
        async with self._session_factory() as session:
            result = await session.execute(
                delete(ToolUsageRow).where(ToolUsageRow.created_at < before)
            )
            await session.commit()
            return result.rowcount or 0

    @staticmethod
    def _usage_filters(stmt, user_id: str | None, project: str | None, since: datetime | None):
        if user_id:
            stmt = stmt.where(ToolUsageRow.user_id == user_id)
        if project:
            stmt = stmt.where(ToolUsageRow.project == project)
        if since:
            stmt = stmt.where(ToolUsageRow.created_at >= since)
        return stmt

    # ── Memory Relations ─────────────────────────────────────────────────────

    async def relation_add(self, relation: MemoryRelation) -> tuple[MemoryRelation, bool]:
        """Insert a relation edge. Returns (relation, created).

        If an identical edge already exists (same user, endpoints, and type),
        the existing edge is returned with created=False.
        """
        async with self._session_factory() as session:
            row = MemoryRelationRow(
                id=relation.id,
                user_id=relation.user_id,
                source_item_id=relation.source_item_id,
                target_item_id=relation.target_item_id,
                relation_type=relation.relation_type,
                project=relation.project,
                metadata_=relation.metadata,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                stmt = select(MemoryRelationRow).where(
                    (MemoryRelationRow.user_id == relation.user_id)
                    & (MemoryRelationRow.source_item_id == relation.source_item_id)
                    & (MemoryRelationRow.target_item_id == relation.target_item_id)
                    & (MemoryRelationRow.relation_type == relation.relation_type)
                )
                result = await session.execute(stmt)
                existing = result.scalars().first()
                if existing is None:  # pragma: no cover — lost a race with a delete
                    raise
                return self._row_to_relation(existing), False
            await session.refresh(row)
            return self._row_to_relation(row), True

    async def relation_list(
        self,
        user_id: str,
        item_id: str | None = None,
        item_ids: list[str] | None = None,
        relation_type: str | None = None,
        direction: str = "both",
        limit: int = 200,
    ) -> list[MemoryRelation]:
        """List edges touching the given item(s), scoped to the user.

        direction: 'out' (item is source), 'in' (item is target), or 'both'.
        """
        ids = list(item_ids or ([item_id] if item_id else []))
        async with self._session_factory() as session:
            stmt = select(MemoryRelationRow).where(MemoryRelationRow.user_id == user_id)
            if ids:
                if direction == "out":
                    stmt = stmt.where(MemoryRelationRow.source_item_id.in_(ids))
                elif direction == "in":
                    stmt = stmt.where(MemoryRelationRow.target_item_id.in_(ids))
                else:
                    stmt = stmt.where(or_(
                        MemoryRelationRow.source_item_id.in_(ids),
                        MemoryRelationRow.target_item_id.in_(ids),
                    ))
            if relation_type:
                stmt = stmt.where(MemoryRelationRow.relation_type == relation_type)
            stmt = stmt.order_by(MemoryRelationRow.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            return [self._row_to_relation(r) for r in result.scalars().all()]

    async def relation_list_all(
        self,
        item_ids: list[str],
        limit: int = 500,
    ) -> list[MemoryRelation]:
        """List edges touching the given items across ALL users — dashboard use only."""
        if not item_ids:
            return []
        async with self._session_factory() as session:
            stmt = (
                select(MemoryRelationRow)
                .where(or_(
                    MemoryRelationRow.source_item_id.in_(item_ids),
                    MemoryRelationRow.target_item_id.in_(item_ids),
                ))
                .order_by(MemoryRelationRow.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [self._row_to_relation(r) for r in result.scalars().all()]

    async def relation_delete(
        self,
        user_id: str,
        relation_id: str | None = None,
        source_item_id: str | None = None,
        target_item_id: str | None = None,
        relation_type: str | None = None,
    ) -> int:
        """Delete edges by relation id, or by endpoint pair (optionally typed).

        Returns the number of deleted rows.
        """
        if not relation_id and not (source_item_id and target_item_id):
            raise ValueError("Provide relation_id, or both source_item_id and target_item_id")
        async with self._session_factory() as session:
            stmt = delete(MemoryRelationRow).where(MemoryRelationRow.user_id == user_id)
            if relation_id:
                stmt = stmt.where(MemoryRelationRow.id == relation_id)
            else:
                stmt = stmt.where(
                    (MemoryRelationRow.source_item_id == source_item_id)
                    & (MemoryRelationRow.target_item_id == target_item_id)
                )
                if relation_type:
                    stmt = stmt.where(MemoryRelationRow.relation_type == relation_type)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount or 0

    async def relation_delete_for_items(self, item_ids: list[str]) -> int:
        """Remove all edges touching the given items — GC/hard-delete cleanup.

        Not user-scoped: called when the items themselves are permanently
        deleted, so any edge referencing them is dangling regardless of owner.
        """
        if not item_ids:
            return 0
        async with self._session_factory() as session:
            stmt = delete(MemoryRelationRow).where(or_(
                MemoryRelationRow.source_item_id.in_(item_ids),
                MemoryRelationRow.target_item_id.in_(item_ids),
            ))
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount or 0

    @staticmethod
    def _row_to_relation(row: MemoryRelationRow) -> MemoryRelation:
        return MemoryRelation(
            id=row.id,
            user_id=row.user_id,
            source_item_id=row.source_item_id,
            target_item_id=row.target_item_id,
            relation_type=row.relation_type,
            project=row.project,
            metadata=row.metadata_ or {},
            created_at=row.created_at,
        )

    # ── Skill CRUD ───────────────────────────────────────────────────────────

    async def skill_store(self, skill: Skill) -> Skill:
        async with self._session_factory() as session:
            row = SkillRow(
                id=skill.id,
                name=skill.name,
                slug=skill.slug,
                description=skill.description,
                content=skill.content,
                frontmatter=skill.frontmatter,
                project=skill.project,
                user_id=skill.user_id,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return self._row_to_skill(row)

    async def skill_get_by_name(self, name: str, user_id: str, project: str | None = None) -> Skill | None:
        async with self._session_factory() as session:
            slug = _slugify(name)
            stmt = select(SkillRow).where(
                (SkillRow.user_id == user_id) &
                ((SkillRow.name == name) | (SkillRow.slug == slug))
            )
            if project is not None:
                stmt = stmt.where(SkillRow.project == project)
            result = await session.execute(stmt)
            row = result.scalars().first()
        return self._row_to_skill(row) if row else None

    async def skill_get_by_id(self, skill_id: str, user_id: str) -> Skill | None:
        async with self._session_factory() as session:
            row = await session.get(SkillRow, skill_id)
        if row and row.user_id == user_id:
            return self._row_to_skill(row)
        return None

    async def skill_list(self, user_id: str, project: str | None = None, limit: int = 50) -> list[Skill]:
        async with self._session_factory() as session:
            stmt = select(SkillRow).where(SkillRow.user_id == user_id)
            if project is not None:
                stmt = stmt.where(SkillRow.project == project)
            stmt = stmt.order_by(SkillRow.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            return [self._row_to_skill(r) for r in result.scalars().all()]

    async def skill_delete(self, skill_id: str, user_id: str) -> bool:
        async with self._session_factory() as session:
            row = await session.get(SkillRow, skill_id)
            if not row or row.user_id != user_id:
                return False
            await session.delete(row)
            await session.commit()
        return True

    @staticmethod
    def _row_to_skill(row: SkillRow) -> Skill:
        return Skill(
            id=row.id,
            name=row.name,
            slug=row.slug,
            description=row.description,
            content=row.content,
            frontmatter=row.frontmatter or {},
            project=row.project,
            user_id=row.user_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def close(self) -> None:
        await self._engine.dispose()

