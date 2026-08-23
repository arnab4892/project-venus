"""Database engine/connection helpers.

A single place to build a SQLAlchemy ``Engine`` from :class:`agentkit.config.Settings`
so tools, the seed loader and the CLI share one connection story.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Connection, Engine, create_engine

from agentkit.config import get_settings


@lru_cache
def get_engine() -> Engine:
    """Return a cached ``Engine`` bound to ``Settings.database_url``."""
    return create_engine(get_settings().database_url)


def connect() -> Connection:
    """Open a new connection on the shared engine (caller manages the transaction)."""
    return get_engine().connect()
