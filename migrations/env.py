"""Alembic environment.

The database URL comes from ``agentkit.config.Settings`` (env / ``.env``),
overriding whatever is in ``alembic.ini`` so no secret is committed. ``target_metadata``
is ``None`` for now — the SQLAlchemy models land with migration ``0001_init``
(LLD-DB-01); until then migrations are hand-written.

Note on ``version_table_schema``: Alembic's bookkeeping table lives in the
default ``public`` schema. It cannot live in ``ops`` yet because ``ops`` is
created *by* migration ``0000_bootstrap`` — putting the version table there would
be a chicken-and-egg on a fresh database. Revisit when ``0001_init`` lands.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from agentkit.config import get_settings

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL, no DBAPI connection)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
