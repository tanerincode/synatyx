from __future__ import annotations

import asyncio
from collections.abc import Callable

import asyncpg  # type: ignore[import-untyped]
from sqlalchemy.engine import URL

from tests.integration.conftest import run_alembic

HANDOFF_TABLES = {"handoffs", "handoff_versions", "handoff_reports"}
BASE_REVISION = "c5a7e31b9d48"  # the revision before add_handoffs


async def _schema(url: URL) -> tuple[set[str], set[str]]:
    conn = await asyncpg.connect(
        host=url.host, port=url.port or 5432, user=url.username,
        password=url.password, database=url.database,
    )
    try:
        tables = {
            r["table_name"]
            for r in await conn.fetch(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            )
        }
        task_cols = {
            r["column_name"]
            for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name='tasks'"
            )
        }
    finally:
        await conn.close()
    return tables, task_cols


def test_messages_upgrade_downgrade(scratch_db_factory: Callable[[str], URL]) -> None:
    url = scratch_db_factory("migrations_messages")

    run_alembic(url, "upgrade", "head")
    tables, _ = asyncio.run(_schema(url))
    assert "messages" in tables

    run_alembic(url, "downgrade", "e2b9a4c7d615")  # the revision before add_messages
    tables, _ = asyncio.run(_schema(url))
    assert "messages" not in tables
    assert HANDOFF_TABLES <= tables

    run_alembic(url, "upgrade", "head")
    tables, _ = asyncio.run(_schema(url))
    assert "messages" in tables


def test_upgrade_downgrade_upgrade(scratch_db_factory: Callable[[str], URL]) -> None:
    url = scratch_db_factory("migrations")

    run_alembic(url, "upgrade", "head")
    tables, task_cols = asyncio.run(_schema(url))
    assert HANDOFF_TABLES <= tables
    assert "assignee" in task_cols

    run_alembic(url, "downgrade", BASE_REVISION)
    tables, task_cols = asyncio.run(_schema(url))
    assert not (HANDOFF_TABLES & tables)
    assert "assignee" not in task_cols
    assert "tasks" in tables  # older tables survive

    run_alembic(url, "upgrade", "head")
    tables, task_cols = asyncio.run(_schema(url))
    assert HANDOFF_TABLES <= tables
    assert "assignee" in task_cols
