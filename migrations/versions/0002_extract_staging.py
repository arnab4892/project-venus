"""extract staging: release_candidate ledger + extraction marker columns

Milestone 3a additions on top of ``0001_init``:

* **``staging.release_candidate``** — a minimal RC lifecycle ledger
  (``id, client, created_at, status``). ``status`` moves through
  ``open → exported → imported → promoted`` (or ``abandoned``).

* Three extraction markers on every ``staging.*`` content mirror:
  ``conflict_group`` (links unresolved source conflicts such as the 20000 vs
  25000 Nm3/hr process-gas capacity, extracted as two rows, not adjudicated),
  ``needs_family`` (a row the frozen family list could not place — never given
  an invented id), and ``section_id`` (which converted section the row came
  from, for evidence provenance).

**Design note (folds into LLD-DB-06 via the clarification path, not /prd-change
— added implementation detail, no requirement/HLD decision change).** The
staging mirrors deliberately carry **no** foreign key to ``release_candidate``:
staging is the waiting room for unreviewed/rejected rows, and RC validity is
enforced at the release gate, not here (LLD-REL). Concretely, ``release
promote`` (milestone 3b) must refuse any RC that has no ledger row or whose
status is not ``exported``/``imported`` — a status-aware check an FK could not
give us. The ingest bootstrap RC (``rc.<client>.bootstrap``) therefore stays
valid without a ledger row.

Revision ID: 0002_extract_staging
Revises: 0001_init
Create Date: 2026-08-28
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_extract_staging"
down_revision: Union[str, None] = "0001_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STAGING = "staging"

RC_STATUSES = ("open", "exported", "imported", "promoted", "abandoned")

# The seven staging content mirrors that gain the extraction marker columns.
_STAGING_TABLES = [
    "document", "product_family", "product", "capability_row",
    "capability_gas", "company_fact", "office",
]

# New marker columns added to each staging mirror.
_MARKER_COLUMNS = ("conflict_group", "needs_family", "section_id")


def _txt() -> sa.Text:
    return sa.Text()


def _ts() -> postgresql.TIMESTAMP:
    return postgresql.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "release_candidate",
        sa.Column("id", _txt(), nullable=False),
        sa.Column("client", _txt(), nullable=False),
        sa.Column("created_at", _ts(), nullable=False, server_default=sa.text("now()")),
        sa.Column("status", _txt(), nullable=False, server_default=sa.text("'open'")),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN " + str(RC_STATUSES), name="ck_release_candidate_status"
        ),
        schema=STAGING,
    )

    # Extraction markers on the content mirrors. Nullable / defaulted so existing
    # bootstrap-RC document rows are unaffected. No FK to release_candidate by
    # design — RC validity is a release-gate check, not a staging invariant
    # (see the module docstring / LLD-REL, milestone 3b).
    for table in _STAGING_TABLES:
        op.add_column(
            table, sa.Column("conflict_group", _txt(), nullable=True), schema=STAGING
        )
        op.add_column(
            table,
            sa.Column(
                "needs_family",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            schema=STAGING,
        )
        op.add_column(
            table, sa.Column("section_id", _txt(), nullable=True), schema=STAGING
        )


def downgrade() -> None:
    for table in _STAGING_TABLES:
        for col in _MARKER_COLUMNS:
            op.drop_column(table, col, schema=STAGING)
    op.drop_table("release_candidate", schema=STAGING)
