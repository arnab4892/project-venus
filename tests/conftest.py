"""Shared pytest fixtures.

Milestone-1 tests are integration tests against the local Postgres
(`docker compose up -d`). When the database is unreachable the DB-backed
fixtures ``skip`` so the pure-Python suite (e.g. ``test_cli``) still runs
anywhere.

``engine`` upgrades the dev database to ``head`` once per session so the suite
is self-sufficient. ``seeded_conn`` opens a transaction, loads the demo seed
into it and rolls back at teardown — no test ever persists rows.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.exc import OperationalError

from agentkit.cli import _alembic_config
from agentkit.config import get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]


def _db_reachable(url: str) -> bool:
    try:
        eng = create_engine(url)
        with eng.connect():
            pass
        eng.dispose()
        return True
    except OperationalError:
        return False


@pytest.fixture(scope="session")
def engine() -> Engine:
    """Session engine bound to the dev database, migrated to ``head``.

    Skips the whole DB suite when Postgres is not up.
    """
    url = get_settings().database_url
    if not _db_reachable(url):
        pytest.skip(f"Postgres not reachable at {url} — run `docker compose up -d`")
    command.upgrade(_alembic_config(), "head")
    eng = create_engine(url)
    yield eng
    eng.dispose()


@pytest.fixture()
def seeded_conn(engine: Engine) -> Connection:
    """A connection inside a rolled-back transaction, pre-loaded with the demo seed."""
    from agentkit.release.seed import seed_demo

    conn = engine.connect()
    trans = conn.begin()
    try:
        seed_demo(conn, client="jyotech")
        yield conn
    finally:
        trans.rollback()
        conn.close()
