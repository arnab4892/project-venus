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


@pytest.fixture(autouse=True, scope="session")
def _tracing_off() -> None:
    """Force Langfuse tracing off for the whole test run, regardless of any LANGFUSE_*/env.

    Enforced in code, not by convention: tests must never emit traces (they run turns in
    rolled-back savepoints, exactly like the eval runner).
    """
    from agentkit.runtime.tracing import force_off

    force_off()


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


# --- runtime turn fixtures (shared by tests/runtime and tests/agents) -------

@pytest.fixture()
def make_ctx(seeded_conn):
    """Factory: build a runtime ``Ctx`` with a given ``FakeLLM`` (+ optional embed seam).

    Loads and activates the runtime prompt manifest so the orchestrator reads DB-versioned
    prompts (not the file fallback).
    """
    from agentkit.runtime.orchestrator import Ctx
    from tests.runtime._helpers import activate_all_prompts

    activate_all_prompts(seeded_conn)

    def _make(fake_llm, *, embed=None, gas_aliases=None, settings=None):
        from agentkit.client_config import gas_alias_map
        from agentkit.config import get_settings

        return Ctx(
            conn=seeded_conn,
            client="jyotech",
            complete=fake_llm,
            embed=embed,
            settings=settings if settings is not None else get_settings(),
            gas_aliases=gas_aliases if gas_aliases is not None else gas_alias_map("jyotech"),
        )

    return _make


@pytest.fixture()
def new_session(seeded_conn):
    """Factory: create an ops.session on the seeded release and return its id."""
    from agentkit.runtime.ops import create_session

    def _make():
        return create_session(seeded_conn, "jyotech", "r2026.08.1")

    return _make
