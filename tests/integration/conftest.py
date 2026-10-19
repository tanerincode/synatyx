"""Postgres integration fixtures.

A scratch database ``synatyx_it_<pid>`` is created on the server named by
``SYNATYX_IT_DATABASE_URL`` (default: the docker-compose.local.yml Postgres),
migrated to head, and dropped at the end of the session. If the server is
unreachable every test in this folder is skipped, so ``make check`` stays green
on machines without docker.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]
import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url

from src.storage.postgres import PostgresStorage

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "postgresql://context_engine:context_engine@localhost:5432/context_engine"
ADMIN_URL_ENV = "SYNATYX_IT_DATABASE_URL"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    here = str(Path(__file__).parent)
    for item in items:
        if str(item.path).startswith(here):
            item.add_marker(pytest.mark.integration)


def _admin_url() -> URL:
    raw = os.environ.get(ADMIN_URL_ENV, DEFAULT_URL)
    return make_url(raw).set(drivername="postgresql")


async def _admin_execute(url: URL, statement: str) -> None:
    conn = await asyncpg.connect(
        host=url.host,
        port=url.port or 5432,
        user=url.username,
        password=url.password,
        database=url.database,
        timeout=3,
    )
    try:
        await conn.execute(statement)
    finally:
        await conn.close()


def run_alembic(db_url: URL, *args: str) -> None:
    """Run alembic against db_url (env.py reads POSTGRES_* from the environment)."""
    env = {
        **os.environ,
        "POSTGRES_HOST": db_url.host or "localhost",
        "POSTGRES_PORT": str(db_url.port or 5432),
        "POSTGRES_USER": db_url.username or "",
        "POSTGRES_PASSWORD": db_url.password or "",
        "POSTGRES_DB": db_url.database or "",
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT, env=env, check=True, capture_output=True, text=True,
    )


@pytest.fixture(scope="session")
def scratch_db_factory() -> Iterator[Callable[[str], URL]]:
    """Create uniquely named scratch databases; drop them all at session end."""
    admin = _admin_url()
    try:
        asyncio.run(_admin_execute(admin, "SELECT 1"))
    except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
        pytest.skip(f"Postgres not reachable via {ADMIN_URL_ENV} ({exc.__class__.__name__})")

    created: list[str] = []

    def make(suffix: str) -> URL:
        name = f"synatyx_it_{os.getpid()}_{suffix}"
        asyncio.run(_admin_execute(admin, f'CREATE DATABASE "{name}"'))
        created.append(name)
        return admin.set(database=name)

    try:
        yield make
    finally:
        for name in created:
            asyncio.run(_admin_execute(admin, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture(scope="session")
def migrated_db_url(scratch_db_factory: Callable[[str], URL]) -> URL:
    url = scratch_db_factory("main")
    run_alembic(url, "upgrade", "head")
    return url


@pytest.fixture
async def storage(migrated_db_url: URL) -> AsyncIterator[PostgresStorage]:
    dsn = migrated_db_url.set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )
    pg = PostgresStorage(dsn)
    async with pg._engine.begin() as conn:
        await conn.execute(text("TRUNCATE handoffs, tasks CASCADE"))
    try:
        yield pg
    finally:
        await pg.close()
