"""LLD-DB-01 — the schema migrations have a working downgrade.

Runs ``upgrade head → downgrade base → upgrade head`` on a throwaway database
(``jyotech_migtest``) created and dropped by the test, so it never touches the
dev database. Skips when Postgres is unreachable.

The dev database URL is redirected to the throwaway DB via the ``DATABASE_URL``
env var (which ``migrations/env.py`` reads through ``agentkit.config``); a hard
guard asserts the redirect took effect before any destructive ``downgrade``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, make_url, text
from sqlalchemy.exc import OperationalError

from agentkit.config import get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_DB = "jyotech_migtest"


def _alembic_config(url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _schema_exists(conn: Connection, schema: str) -> bool:
    return conn.execute(
        text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
        {"s": schema},
    ).first() is not None


def _table_exists(conn: Connection, schema: str, table: str) -> bool:
    return conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = :s AND table_name = :t"
        ),
        {"s": schema, "t": table},
    ).first() is not None


def _view_exists(conn: Connection, schema: str, view: str) -> bool:
    return conn.execute(
        text(
            "SELECT 1 FROM information_schema.views "
            "WHERE table_schema = :s AND table_name = :t"
        ),
        {"s": schema, "t": view},
    ).first() is not None


def test_migration_roundtrip(monkeypatch):
    base = make_url(get_settings().database_url)
    admin_url = base.set(database="postgres")
    test_url = base.set(database=TEST_DB)

    # --- reachability check / skip ---
    try:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect():
            pass
    except OperationalError:
        pytest.skip("Postgres not reachable — run `docker compose up -d`")

    def _drop_db() -> None:
        with admin.connect() as c:
            c.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :d AND pid <> pg_backend_pid()"
                ),
                {"d": TEST_DB},
            )
            c.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}"'))

    _drop_db()
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{TEST_DB}"'))

    try:
        # Redirect the migration engine to the throwaway DB and prove the redirect.
        # NB: str(URL) masks the password as "***"; render explicitly.
        test_url_str = test_url.render_as_string(hide_password=False)
        monkeypatch.setenv("DATABASE_URL", test_url_str)
        get_settings.cache_clear()
        assert TEST_DB in get_settings().database_url, "redirect to throwaway DB failed"

        cfg = _alembic_config(test_url_str)
        eng = create_engine(test_url)

        # upgrade → all schema objects present
        command.upgrade(cfg, "head")
        with eng.connect() as c:
            assert _table_exists(c, "facts", "capability_row")
            assert _table_exists(c, "facts", "capability_gas")
            assert _view_exists(c, "facts", "active_capability_row")
            assert _table_exists(c, "staging", "capability_row")

        # downgrade to base → 0001 tables gone and 0000 drops the schemas
        command.downgrade(cfg, "base")
        with eng.connect() as c:
            assert not _schema_exists(c, "facts")
            assert not _schema_exists(c, "staging")

        # upgrade again → back to a full schema (round-trip is clean)
        command.upgrade(cfg, "head")
        with eng.connect() as c:
            assert _table_exists(c, "facts", "capability_row")
            assert _view_exists(c, "facts", "active_capability_gas")

        eng.dispose()
    finally:
        get_settings.cache_clear()
        _drop_db()
        admin.dispose()
