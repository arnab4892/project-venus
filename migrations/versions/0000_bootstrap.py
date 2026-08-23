"""bootstrap: pgvector extension + empty schemas

Creates the ``vector`` extension and the four schemas the data model uses
(``facts``, ``vec``, ``staging``, ``ops``). No tables — those arrive with
``0001_init`` (LLD-DB-01). This migration only makes ``agentkit db upgrade``
verifiable end to end without pre-empting the data model.

Revision ID: 0000_bootstrap
Revises:
Create Date: 2026-08-23

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0000_bootstrap"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMAS = ("facts", "vec", "staging", "ops")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    for schema in SCHEMAS:
        op.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')


def downgrade() -> None:
    for schema in reversed(SCHEMAS):
        op.execute(f'DROP SCHEMA IF EXISTS "{schema}" RESTRICT')
    op.execute("DROP EXTENSION IF EXISTS vector")
