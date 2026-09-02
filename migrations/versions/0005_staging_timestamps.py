"""staging timestamps: created_at / updated_at on the content mirrors + RC ledger

Housekeeping addition on top of ``0004_ops``. Gives the ``staging.*`` waiting room a
chronology it did not have: ``created_at`` and ``updated_at`` (both ``timestamptz``,
server-defaulted to ``now()``) on the seven content mirrors, and ``updated_at`` on
``staging.release_candidate`` (which already carries ``created_at`` from ``0002``).

**Scope — staging only, by design.** ``facts.*`` stays timestamp-free: fact rows are
immutable and their history is ``facts.release.built_at`` plus the release diff, not a
per-row mutation stamp. ``ops.*`` supplies its own timestamps explicitly and is left
alone. Only ``staging.*`` — where rows are rewritten during extraction/import/review —
gains mutation timestamps.

**How ``updated_at`` is maintained.** All staging writes are raw SQL (no ORM, so a
SQLAlchemy ``onupdate`` could never fire) and there is no trigger convention in this
codebase. The columns default to ``now()`` on INSERT (so ``created_at == updated_at``
at birth); the in-place UPDATE sites set ``updated_at = clock_timestamp()`` explicitly
(``extract/staging_write.py`` rewrites are delete-then-reinsert, so they get a fresh
``created_at`` for free). ``clock_timestamp()`` (not ``now()``) is used on update so the
stamp is the real modification instant and strictly advances even within one transaction.

**Backfill caveat.** Existing rows written before this migration receive
``created_at = updated_at = migration time`` from the server default — so timestamps
predating ``0005`` are *not* true chronology; only rows written after it carry real
creation history.

Reversible: ``downgrade`` drops the columns.

Revision ID: 0005_staging_timestamps
Revises: 0004_ops
Create Date: 2026-09-02
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005_staging_timestamps"
down_revision: Union[str, None] = "0004_ops"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STAGING = "staging"

# The seven staging content mirrors (same canonical list as 0001/0002 and
# extract/staging_write.py) — each gains both created_at and updated_at.
_STAGING_TABLES = [
    "document", "product_family", "product", "capability_row",
    "capability_gas", "company_fact", "office",
]

_NEW_COLUMNS = ("created_at", "updated_at")


def _ts() -> postgresql.TIMESTAMP:
    return postgresql.TIMESTAMP(timezone=True)


def upgrade() -> None:
    # Seven content mirrors: both columns, server-defaulted to now() (created == updated
    # at birth). NOT NULL is safe — the default backfills any existing rows.
    for table in _STAGING_TABLES:
        op.add_column(
            table,
            sa.Column("created_at", _ts(), nullable=False, server_default=sa.text("now()")),
            schema=STAGING,
        )
        op.add_column(
            table,
            sa.Column("updated_at", _ts(), nullable=False, server_default=sa.text("now()")),
            schema=STAGING,
        )

    # release_candidate already has created_at (0002) — add updated_at only.
    op.add_column(
        "release_candidate",
        sa.Column("updated_at", _ts(), nullable=False, server_default=sa.text("now()")),
        schema=STAGING,
    )


def downgrade() -> None:
    op.drop_column("release_candidate", "updated_at", schema=STAGING)
    for table in _STAGING_TABLES:
        for col in _NEW_COLUMNS:
            op.drop_column(table, col, schema=STAGING)
