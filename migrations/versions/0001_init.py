"""init: facts / staging tables + active_* views

Creates every table in ``facts.*`` and ``staging.*`` exactly as
``docs/design/data-model.md`` §2-3 specifies, implementing:

* **LLD-DB-01** — all tables/columns for the data model (schemas + extension are
  0000's job; this migration adds no ``vec.*`` tables — those are per-release,
  built by the embedder, LLD-DB-03).
* **LLD-DB-02** — every ``facts.*`` table carries ``release_id``; the primary key
  is the composite ``(natural_id, release_id)`` so releases coexist for
  promote/rollback; a ``facts.active_<table>`` view per table exposes the natural
  ids of the single active release, and a partial unique index enforces exactly
  one ``release.is_active``.
* **LLD-DB-06** — ``staging.*`` mirrors the seven content tables plus
  ``evidence/confidence/review_status/reviewer/reviewed_at``, keyed by
  ``release_candidate_id``. Staging carries *no* cross-table foreign keys: it
  holds unreviewed and rejected rows, and FK closure is a release-import gate
  (LLD-REL-03), not a staging invariant.

Revision ID: 0001_init
Revises: 0000_bootstrap
Create Date: 2026-08-23
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_init"
down_revision: Union[str, None] = "0000_bootstrap"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FACTS = "facts"
STAGING = "staging"

REVIEW_STATUSES = ("pending", "approved", "edited", "rejected")
COMPANY_FACT_KINDS = (
    "certification", "founded", "founder", "facility",
    "industry_served", "client", "coverage", "contact",
)


def _txt() -> sa.Text:
    return sa.Text()


def _arr() -> postgresql.ARRAY:
    return postgresql.ARRAY(sa.Text())


def _ts() -> postgresql.TIMESTAMP:
    return postgresql.TIMESTAMP(timezone=True)


# ---------------------------------------------------------------------------
# facts.* — strict, release-keyed
# ---------------------------------------------------------------------------

def _create_facts() -> None:
    op.create_table(
        "release",
        sa.Column("release_id", _txt(), nullable=False),
        sa.Column("built_at", _ts(), nullable=True),
        sa.Column("source_manifest", postgresql.JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("notes", _txt(), nullable=True),
        sa.PrimaryKeyConstraint("release_id"),
        schema=FACTS,
    )
    # exactly one active release
    op.create_index(
        "uq_release_one_active", "release", ["is_active"],
        unique=True, schema=FACTS, postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "document",
        sa.Column("doc_id", _txt(), nullable=False),
        sa.Column("release_id", _txt(), nullable=False),
        sa.Column("kind", _txt(), nullable=False),
        sa.Column("title", _txt(), nullable=True),
        sa.Column("url", _txt(), nullable=True),
        sa.Column("division", _txt(), nullable=True),
        sa.Column("sha256", _txt(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("doc_id", "release_id"),
        sa.ForeignKeyConstraint(["release_id"], [f"{FACTS}.release.release_id"]),
        schema=FACTS,
    )

    op.create_table(
        "product_family",
        sa.Column("family_id", _txt(), nullable=False),
        sa.Column("release_id", _txt(), nullable=False),
        sa.Column("division", _txt(), nullable=True),
        sa.Column("category", _txt(), nullable=True),
        sa.Column("name", _txt(), nullable=True),
        sa.Column("summary", _txt(), nullable=True),
        sa.Column("applications", _arr(), nullable=True),
        sa.Column("standards", _arr(), nullable=True),
        sa.Column("source_doc_id", _txt(), nullable=False),
        sa.Column("source_locator", _txt(), nullable=True),
        sa.PrimaryKeyConstraint("family_id", "release_id"),
        sa.ForeignKeyConstraint(["release_id"], [f"{FACTS}.release.release_id"]),
        sa.ForeignKeyConstraint(
            ["source_doc_id", "release_id"],
            [f"{FACTS}.document.doc_id", f"{FACTS}.document.release_id"],
        ),
        schema=FACTS,
    )

    op.create_table(
        "product",
        sa.Column("product_id", _txt(), nullable=False),
        sa.Column("release_id", _txt(), nullable=False),
        sa.Column("family_id", _txt(), nullable=False),
        sa.Column("model_name", _txt(), nullable=False),
        sa.Column("variant", _txt(), nullable=True),
        sa.Column("description", _txt(), nullable=True),
        sa.Column("attributes", postgresql.JSONB(), nullable=True),
        sa.Column("source_doc_id", _txt(), nullable=False),
        sa.Column("source_locator", _txt(), nullable=True),
        sa.PrimaryKeyConstraint("product_id", "release_id"),
        sa.ForeignKeyConstraint(
            ["family_id", "release_id"],
            [f"{FACTS}.product_family.family_id", f"{FACTS}.product_family.release_id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_doc_id", "release_id"],
            [f"{FACTS}.document.doc_id", f"{FACTS}.document.release_id"],
        ),
        schema=FACTS,
    )

    op.create_table(
        "capability_row",
        sa.Column("cap_id", _txt(), nullable=False),
        sa.Column("release_id", _txt(), nullable=False),
        sa.Column("family_id", _txt(), nullable=False),
        sa.Column("comp_type", _txt(), nullable=True),
        sa.Column("lubricated", sa.Boolean(), nullable=True),
        sa.Column("cooling", _txt(), nullable=True),
        sa.Column("capacity_min", sa.Numeric(), nullable=True),
        sa.Column("capacity_max", sa.Numeric(), nullable=True),
        sa.Column("capacity_unit", _txt(), nullable=True),
        sa.Column("discharge_p_min", sa.Numeric(), nullable=True),
        sa.Column("discharge_p_max", sa.Numeric(), nullable=True),
        sa.Column("pressure_unit", _txt(), nullable=True),
        sa.Column("driver", _arr(), nullable=True),
        sa.Column("standards", _arr(), nullable=True),
        sa.Column("source_doc_id", _txt(), nullable=False),
        sa.Column("source_locator", _txt(), nullable=True),
        sa.PrimaryKeyConstraint("cap_id", "release_id"),
        sa.ForeignKeyConstraint(
            ["family_id", "release_id"],
            [f"{FACTS}.product_family.family_id", f"{FACTS}.product_family.release_id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_doc_id", "release_id"],
            [f"{FACTS}.document.doc_id", f"{FACTS}.document.release_id"],
        ),
        schema=FACTS,
    )

    op.create_table(
        "capability_gas",
        sa.Column("cap_id", _txt(), nullable=False),
        sa.Column("release_id", _txt(), nullable=False),
        sa.Column("gas", _txt(), nullable=False),
        sa.PrimaryKeyConstraint("cap_id", "release_id", "gas"),
        sa.ForeignKeyConstraint(
            ["cap_id", "release_id"],
            [f"{FACTS}.capability_row.cap_id", f"{FACTS}.capability_row.release_id"],
        ),
        schema=FACTS,
    )

    op.create_table(
        "company_fact",
        sa.Column("fact_id", _txt(), nullable=False),
        sa.Column("release_id", _txt(), nullable=False),
        sa.Column("kind", _txt(), nullable=False),
        sa.Column("value", _txt(), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("source_doc_id", _txt(), nullable=False),
        sa.Column("source_locator", _txt(), nullable=True),
        sa.PrimaryKeyConstraint("fact_id", "release_id"),
        sa.CheckConstraint(
            "kind IN " + str(COMPANY_FACT_KINDS), name="ck_company_fact_kind"
        ),
        sa.ForeignKeyConstraint(["release_id"], [f"{FACTS}.release.release_id"]),
        sa.ForeignKeyConstraint(
            ["source_doc_id", "release_id"],
            [f"{FACTS}.document.doc_id", f"{FACTS}.document.release_id"],
        ),
        schema=FACTS,
    )

    op.create_table(
        "office",
        sa.Column("office_id", _txt(), nullable=False),
        sa.Column("release_id", _txt(), nullable=False),
        sa.Column("name", _txt(), nullable=True),
        sa.Column("city", _txt(), nullable=True),
        sa.Column("region", _txt(), nullable=True),
        sa.Column("address", _txt(), nullable=True),
        sa.Column("phone", _txt(), nullable=True),
        sa.Column("email", _txt(), nullable=True),
        sa.Column("serves_divisions", _arr(), nullable=True),
        sa.Column("source_doc_id", _txt(), nullable=False),
        sa.Column("source_locator", _txt(), nullable=True),
        sa.PrimaryKeyConstraint("office_id", "release_id"),
        sa.ForeignKeyConstraint(["release_id"], [f"{FACTS}.release.release_id"]),
        sa.ForeignKeyConstraint(
            ["source_doc_id", "release_id"],
            [f"{FACTS}.document.doc_id", f"{FACTS}.document.release_id"],
        ),
        schema=FACTS,
    )

    op.create_table(
        "region_state",
        sa.Column("state", _txt(), nullable=False),
        sa.Column("release_id", _txt(), nullable=False),
        sa.Column("region", _txt(), nullable=False),
        sa.Column("office_id", _txt(), nullable=False),
        sa.PrimaryKeyConstraint("state", "release_id"),
        sa.ForeignKeyConstraint(["release_id"], [f"{FACTS}.release.release_id"]),
        sa.ForeignKeyConstraint(
            ["office_id", "release_id"],
            [f"{FACTS}.office.office_id", f"{FACTS}.office.release_id"],
        ),
        schema=FACTS,
    )

    op.create_table(
        "chunk",
        sa.Column("chunk_id", _txt(), nullable=False),
        sa.Column("release_id", _txt(), nullable=False),
        sa.Column("doc_id", _txt(), nullable=False),
        sa.Column("locator", _txt(), nullable=True),
        sa.Column("heading_path", _arr(), nullable=True),
        sa.Column("content_md", _txt(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("family_ids", _arr(), nullable=True),
        sa.Column("tsv", postgresql.TSVECTOR(), nullable=True),
        sa.PrimaryKeyConstraint("chunk_id", "release_id"),
        sa.ForeignKeyConstraint(
            ["doc_id", "release_id"],
            [f"{FACTS}.document.doc_id", f"{FACTS}.document.release_id"],
        ),
        schema=FACTS,
    )


# ---------------------------------------------------------------------------
# facts.active_* views — natural ids of the single active release
# ---------------------------------------------------------------------------

# table -> natural columns exposed by its active_* view (release_id omitted)
ACTIVE_VIEW_COLUMNS: dict[str, list[str]] = {
    "release": ["release_id", "built_at", "source_manifest", "is_active", "notes"],
    "document": ["doc_id", "kind", "title", "url", "division", "sha256", "page_count"],
    "product_family": [
        "family_id", "division", "category", "name", "summary",
        "applications", "standards", "source_doc_id", "source_locator",
    ],
    "product": [
        "product_id", "family_id", "model_name", "variant", "description",
        "attributes", "source_doc_id", "source_locator",
    ],
    "capability_row": [
        "cap_id", "family_id", "comp_type", "lubricated", "cooling",
        "capacity_min", "capacity_max", "capacity_unit",
        "discharge_p_min", "discharge_p_max", "pressure_unit",
        "driver", "standards", "source_doc_id", "source_locator",
    ],
    "capability_gas": ["cap_id", "gas"],
    "company_fact": ["fact_id", "kind", "value", "detail", "source_doc_id", "source_locator"],
    "office": [
        "office_id", "name", "city", "region", "address", "phone", "email",
        "serves_divisions", "source_doc_id", "source_locator",
    ],
    "region_state": ["state", "region", "office_id"],
    "chunk": [
        "chunk_id", "doc_id", "locator", "heading_path", "content_md",
        "token_count", "family_ids", "tsv",
    ],
}


def _create_views() -> None:
    for table, cols in ACTIVE_VIEW_COLUMNS.items():
        select_cols = ", ".join(f"t.{c}" for c in cols)
        if table == "release":
            # the release table IS the active filter — no self-join needed
            sql = (
                f"CREATE VIEW {FACTS}.active_release AS "
                f"SELECT {select_cols} FROM {FACTS}.release t WHERE t.is_active"
            )
        else:
            sql = (
                f"CREATE VIEW {FACTS}.active_{table} AS "
                f"SELECT {select_cols} FROM {FACTS}.{table} t "
                f"JOIN {FACTS}.release r ON t.release_id = r.release_id "
                f"WHERE r.is_active"
            )
        op.execute(sql)


def _drop_views() -> None:
    for table in ACTIVE_VIEW_COLUMNS:
        op.execute(f"DROP VIEW IF EXISTS {FACTS}.active_{table}")


# ---------------------------------------------------------------------------
# staging.* — loose mirrors of the seven content tables + review columns
# ---------------------------------------------------------------------------

def _review_columns() -> list[sa.Column]:
    return [
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column("confidence", sa.Numeric(), nullable=True),
        sa.Column(
            "review_status", _txt(), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("reviewer", _txt(), nullable=True),
        sa.Column("reviewed_at", _ts(), nullable=True),
    ]


def _staging_table(name: str, *data_columns: sa.Column, pk: Sequence[str]) -> None:
    op.create_table(
        name,
        sa.Column("release_candidate_id", _txt(), nullable=False),
        *data_columns,
        *_review_columns(),
        sa.PrimaryKeyConstraint("release_candidate_id", *pk),
        sa.CheckConstraint(
            "review_status IN " + str(REVIEW_STATUSES), name=f"ck_{name}_review_status"
        ),
        schema=STAGING,
    )


def _create_staging() -> None:
    _staging_table(
        "document",
        sa.Column("doc_id", _txt(), nullable=False),
        sa.Column("kind", _txt(), nullable=True),
        sa.Column("title", _txt(), nullable=True),
        sa.Column("url", _txt(), nullable=True),
        sa.Column("division", _txt(), nullable=True),
        sa.Column("sha256", _txt(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        pk=["doc_id"],
    )
    _staging_table(
        "product_family",
        sa.Column("family_id", _txt(), nullable=False),
        sa.Column("division", _txt(), nullable=True),
        sa.Column("category", _txt(), nullable=True),
        sa.Column("name", _txt(), nullable=True),
        sa.Column("summary", _txt(), nullable=True),
        sa.Column("applications", _arr(), nullable=True),
        sa.Column("standards", _arr(), nullable=True),
        sa.Column("source_doc_id", _txt(), nullable=True),
        sa.Column("source_locator", _txt(), nullable=True),
        pk=["family_id"],
    )
    _staging_table(
        "product",
        sa.Column("product_id", _txt(), nullable=False),
        sa.Column("family_id", _txt(), nullable=True),
        sa.Column("model_name", _txt(), nullable=True),
        sa.Column("variant", _txt(), nullable=True),
        sa.Column("description", _txt(), nullable=True),
        sa.Column("attributes", postgresql.JSONB(), nullable=True),
        sa.Column("source_doc_id", _txt(), nullable=True),
        sa.Column("source_locator", _txt(), nullable=True),
        pk=["product_id"],
    )
    _staging_table(
        "capability_row",
        sa.Column("cap_id", _txt(), nullable=False),
        sa.Column("family_id", _txt(), nullable=True),
        sa.Column("comp_type", _txt(), nullable=True),
        sa.Column("lubricated", sa.Boolean(), nullable=True),
        sa.Column("cooling", _txt(), nullable=True),
        sa.Column("capacity_min", sa.Numeric(), nullable=True),
        sa.Column("capacity_max", sa.Numeric(), nullable=True),
        sa.Column("capacity_unit", _txt(), nullable=True),
        sa.Column("discharge_p_min", sa.Numeric(), nullable=True),
        sa.Column("discharge_p_max", sa.Numeric(), nullable=True),
        sa.Column("pressure_unit", _txt(), nullable=True),
        sa.Column("driver", _arr(), nullable=True),
        sa.Column("standards", _arr(), nullable=True),
        sa.Column("source_doc_id", _txt(), nullable=True),
        sa.Column("source_locator", _txt(), nullable=True),
        pk=["cap_id"],
    )
    _staging_table(
        "capability_gas",
        sa.Column("cap_id", _txt(), nullable=False),
        sa.Column("gas", _txt(), nullable=False),
        pk=["cap_id", "gas"],
    )
    _staging_table(
        "company_fact",
        sa.Column("fact_id", _txt(), nullable=False),
        sa.Column("kind", _txt(), nullable=True),
        sa.Column("value", _txt(), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("source_doc_id", _txt(), nullable=True),
        sa.Column("source_locator", _txt(), nullable=True),
        pk=["fact_id"],
    )
    _staging_table(
        "office",
        sa.Column("office_id", _txt(), nullable=False),
        sa.Column("name", _txt(), nullable=True),
        sa.Column("city", _txt(), nullable=True),
        sa.Column("region", _txt(), nullable=True),
        sa.Column("address", _txt(), nullable=True),
        sa.Column("phone", _txt(), nullable=True),
        sa.Column("email", _txt(), nullable=True),
        sa.Column("serves_divisions", _arr(), nullable=True),
        sa.Column("source_doc_id", _txt(), nullable=True),
        sa.Column("source_locator", _txt(), nullable=True),
        pk=["office_id"],
    )


# tables in dependency order (parents first)
_FACTS_TABLES_IN_ORDER = [
    "release", "document", "product_family", "product",
    "capability_row", "capability_gas", "company_fact", "office",
    "region_state", "chunk",
]
_STAGING_TABLES = [
    "document", "product_family", "product", "capability_row",
    "capability_gas", "company_fact", "office",
]


def upgrade() -> None:
    _create_facts()
    _create_views()
    _create_staging()


def downgrade() -> None:
    _drop_views()
    for name in _STAGING_TABLES:
        op.drop_table(name, schema=STAGING)
    for name in reversed(_FACTS_TABLES_IN_ORDER):
        op.drop_table(name, schema=FACTS)
