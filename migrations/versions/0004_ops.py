"""ops: operational schema the runtime writes to (LLD-DB-04, data-model §3)

Creates the full ``ops.*`` schema exactly as ``docs/design/data-model.md`` §3
specifies — implementing **LLD-DB-04** (``ops.message`` with ``turn_id`` FK,
``seq_in_turn`` and ``kind ∈ {text, document_card, status, form}``) and standing up
the whole conversational store so the milestone-5a orchestrator has somewhere to
write.

All ten tables are created now even though ``lead`` / ``handoff_dispatch`` are not
written until the handoff milestone (M6): the schema is designed once, not grown
per feature. ``ops.*`` is **append-only** at runtime — the orchestrator inserts,
never updates/deletes; the only mutations are the designed status-transition
columns (``session.ended_at``/``end_reason``, ``lead.status``,
``handoff_dispatch.status``) and the config tables (``client`` /
``prompt_version``) which are managed by the ``prompt load`` / ``activate`` tooling.

The ``ops`` schema itself is created by ``0000_bootstrap``; this migration only adds
tables. ``downgrade`` drops them child-before-parent.

Revision ID: 0004_ops
Revises: 0003_product_model_name_nullable
Create Date: 2026-08-29
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_ops"
down_revision: Union[str, None] = "0003_product_model_name_nullable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OPS = "ops"

MESSAGE_KINDS = ("text", "document_card", "status", "form")


def _txt() -> sa.Text:
    return sa.Text()


def _arr() -> postgresql.ARRAY:
    return postgresql.ARRAY(sa.Text())


def _ts() -> postgresql.TIMESTAMP:
    return postgresql.TIMESTAMP(timezone=True)


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB()


# tables in dependency order (parents first) — used to drive downgrade.
_TABLES_IN_ORDER = [
    "client",
    "prompt_version",
    "session",
    "turn",
    "message",
    "agent_invocation",
    "tool_call",
    "citation",
    "lead",
    "handoff_dispatch",
]


def _create_config() -> None:
    # ops.client (§3.1) — one row per client; config, loaded by `prompt load`.
    op.create_table(
        "client",
        sa.Column("client_id", _txt(), nullable=False),
        sa.Column("name", _txt(), nullable=True),
        sa.Column("domain", _txt(), nullable=True),
        sa.Column("theme", _jsonb(), nullable=True),
        sa.Column("active_release", _txt(), nullable=True),
        sa.Column("handoff_to", _jsonb(), nullable=True),
        sa.Column("created_at", _ts(), nullable=True),
        sa.PrimaryKeyConstraint("client_id"),
        schema=OPS,
    )

    # ops.prompt_version (§3.1) — prompt bodies versioned in the DB; one active per agent.
    op.create_table(
        "prompt_version",
        sa.Column("prompt_id", _txt(), nullable=False),
        sa.Column("client_id", _txt(), nullable=False),
        sa.Column("agent", _txt(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("body", _txt(), nullable=True),
        sa.Column("created_at", _ts(), nullable=True),
        sa.PrimaryKeyConstraint("prompt_id"),
        sa.ForeignKeyConstraint(["client_id"], [f"{OPS}.client.client_id"]),
        schema=OPS,
    )
    # exactly one active prompt version per (client, agent)
    op.create_index(
        "uq_prompt_version_one_active",
        "prompt_version",
        ["client_id", "agent"],
        unique=True,
        schema=OPS,
        postgresql_where=sa.text("is_active"),
    )


def _create_conversation() -> None:
    # ops.session (§3.2). lead_id is a plain nullable column (no FK — session↔lead is
    # circular; the relationship is closed from lead.session_id).
    op.create_table(
        "session",
        sa.Column("session_id", _uuid(), nullable=False),
        sa.Column("client_id", _txt(), nullable=False),
        sa.Column("started_at", _ts(), nullable=True),
        sa.Column("last_seen_at", _ts(), nullable=True),
        sa.Column("ended_at", _ts(), nullable=True),
        sa.Column("release_id", _txt(), nullable=True),
        sa.Column("landing_url", _txt(), nullable=True),
        sa.Column("user_agent", _txt(), nullable=True),
        sa.Column("ip", _txt(), nullable=True),
        sa.Column("cookie_id", _txt(), nullable=True),
        sa.Column("locale_detected", _txt(), nullable=True),
        sa.Column("lead_id", _uuid(), nullable=True),
        sa.Column("end_reason", _txt(), nullable=True),
        sa.PrimaryKeyConstraint("session_id"),
        sa.ForeignKeyConstraint(["client_id"], [f"{OPS}.client.client_id"]),
        schema=OPS,
    )

    # ops.turn (§3.3) — one orchestrator pass; parent of agent + tool logs.
    op.create_table(
        "turn",
        sa.Column("turn_id", _uuid(), nullable=False),
        sa.Column("session_id", _uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=True),
        sa.Column("started_at", _ts(), nullable=True),
        sa.Column("completed_at", _ts(), nullable=True),
        sa.Column("triage", _jsonb(), nullable=True),
        sa.Column("routed_agent", _txt(), nullable=True),
        sa.Column("grounding", _jsonb(), nullable=True),
        sa.Column("outcome", _txt(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("turn_id"),
        sa.ForeignKeyConstraint(["session_id"], [f"{OPS}.session.session_id"]),
        schema=OPS,
    )

    # ops.message (§3.3 / LLD-DB-04) — every bubble; a turn owns 1..n in either role.
    op.create_table(
        "message",
        sa.Column("message_id", _uuid(), nullable=False),
        sa.Column("session_id", _uuid(), nullable=False),
        sa.Column("turn_id", _uuid(), nullable=False),
        sa.Column("seq_in_turn", sa.Integer(), nullable=False),
        sa.Column("role", _txt(), nullable=False),
        sa.Column("kind", _txt(), nullable=False),
        sa.Column("text", _txt(), nullable=True),
        sa.Column("payload", _jsonb(), nullable=True),
        sa.Column("at", _ts(), nullable=True),
        sa.PrimaryKeyConstraint("message_id"),
        sa.CheckConstraint("kind IN " + str(MESSAGE_KINDS), name="ck_message_kind"),
        sa.ForeignKeyConstraint(["session_id"], [f"{OPS}.session.session_id"]),
        sa.ForeignKeyConstraint(["turn_id"], [f"{OPS}.turn.turn_id"]),
        schema=OPS,
    )

    # ops.agent_invocation (§3.4) — records the prompt_version id actually used.
    op.create_table(
        "agent_invocation",
        sa.Column("inv_id", _uuid(), nullable=False),
        sa.Column("turn_id", _uuid(), nullable=False),
        sa.Column("agent", _txt(), nullable=False),
        sa.Column("prompt_id", _txt(), nullable=True),
        sa.Column("input", _jsonb(), nullable=True),
        sa.Column("output", _jsonb(), nullable=True),
        sa.Column("slots", _jsonb(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("inv_id"),
        sa.ForeignKeyConstraint(["turn_id"], [f"{OPS}.turn.turn_id"]),
        sa.ForeignKeyConstraint(["prompt_id"], [f"{OPS}.prompt_version.prompt_id"]),
        schema=OPS,
    )

    # ops.tool_call (§3.4) — logging begins this milestone (LLD-TOOL preamble).
    op.create_table(
        "tool_call",
        sa.Column("call_id", _uuid(), nullable=False),
        sa.Column("inv_id", _uuid(), nullable=False),
        sa.Column("tool", _txt(), nullable=False),
        sa.Column("args", _jsonb(), nullable=True),
        sa.Column("result_summary", _jsonb(), nullable=True),
        sa.Column("rows_returned", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("call_id"),
        sa.ForeignKeyConstraint(["inv_id"], [f"{OPS}.agent_invocation.inv_id"]),
        schema=OPS,
    )

    # ops.citation (§3.5) — one per grounded reference rendered in the reply.
    op.create_table(
        "citation",
        sa.Column("citation_id", _uuid(), nullable=False),
        sa.Column("turn_id", _uuid(), nullable=False),
        sa.Column("kind", _txt(), nullable=True),
        sa.Column("ref_id", _txt(), nullable=True),
        sa.Column("locator", _txt(), nullable=True),
        sa.Column("url", _txt(), nullable=True),
        sa.PrimaryKeyConstraint("citation_id"),
        sa.ForeignKeyConstraint(["turn_id"], [f"{OPS}.turn.turn_id"]),
        schema=OPS,
    )


def _create_handoff() -> None:
    # ops.lead (§3.6) — written only from M6; schema created now.
    op.create_table(
        "lead",
        sa.Column("lead_id", _uuid(), nullable=False),
        sa.Column("session_id", _uuid(), nullable=False),
        sa.Column("lead_type", _txt(), nullable=True),
        sa.Column("division", _txt(), nullable=True),
        sa.Column("contact", _jsonb(), nullable=True),
        sa.Column("consent_at", _ts(), nullable=True),
        sa.Column("enquiry", _jsonb(), nullable=True),
        sa.Column("matched_family_id", _txt(), nullable=True),
        sa.Column("route_to", _arr(), nullable=True),
        sa.Column("region", _txt(), nullable=True),
        sa.Column("reference_no", _txt(), nullable=True),
        sa.Column("status", _txt(), nullable=True),
        sa.PrimaryKeyConstraint("lead_id"),
        sa.ForeignKeyConstraint(["session_id"], [f"{OPS}.session.session_id"]),
        schema=OPS,
    )

    # ops.handoff_dispatch (§3.6). "to" is a SQL reserved word — SQLAlchemy quotes it.
    op.create_table(
        "handoff_dispatch",
        sa.Column("dispatch_id", _uuid(), nullable=False),
        sa.Column("lead_id", _uuid(), nullable=False),
        sa.Column("channel", _txt(), nullable=False, server_default=sa.text("'email'")),
        sa.Column("to", _txt(), nullable=True),
        sa.Column("subject", _txt(), nullable=True),
        sa.Column("body_md", _txt(), nullable=True),
        sa.Column("sent_at", _ts(), nullable=True),
        sa.Column("provider_msg_id", _txt(), nullable=True),
        sa.Column("status", _txt(), nullable=True),
        sa.PrimaryKeyConstraint("dispatch_id"),
        sa.ForeignKeyConstraint(["lead_id"], [f"{OPS}.lead.lead_id"]),
        schema=OPS,
    )


def upgrade() -> None:
    _create_config()
    _create_conversation()
    _create_handoff()


def downgrade() -> None:
    for name in reversed(_TABLES_IN_ORDER):
        op.drop_table(name, schema=OPS)
