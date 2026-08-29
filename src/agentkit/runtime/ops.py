"""Insert-only writers for the ``ops.*`` conversational store (LLD-RT-02/06, data-model §3).

The orchestrator computes a whole turn in memory, then flushes it here in FK order:
``turn → message → agent_invocation → tool_call → citation``. Writing atomically at the end
of a turn keeps ``ops.*`` **append-only** — there are no UPDATE paths for conversation rows
(the only mutations anywhere are the designed status columns: ``session.ended_at`` /
``end_reason``). The SSE write-before-emit ordering of LLD-RT-06 is realised for the CLI as
one atomic flush per turn; token-level streaming order is an API-milestone concern.

The ``ops`` rows are the single source of truth for a conversation — ``rebuild_context`` can
reconstruct the full history for ``chat --session <id>`` from them alone, with no checkpointer
(LLD-RT-02 deferral).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from sqlalchemy import Connection, text


# ---------------------------------------------------------------------------
# In-memory records assembled by the orchestrator (no DB writes until flush).
# ---------------------------------------------------------------------------

@dataclass
class ToolCallRecord:
    tr_id: str  # ephemeral, turn-local id used to link citations to a tool result
    tool: str
    args: dict
    result: dict
    rows_returned: int
    latency_ms: int


@dataclass
class InvocationRecord:
    agent: str
    prompt_id: str | None = None
    input: dict | None = None
    output: dict | None = None
    slots: dict | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    tool_calls: list[ToolCallRecord] = field(default_factory=list)


@dataclass
class MessageRecord:
    role: str
    kind: str
    text: str | None = None
    payload: dict | None = None


@dataclass
class CitationRecord:
    kind: str
    ref_id: str
    locator: str | None = None
    url: str | None = None


@dataclass
class TurnRecord:
    session_id: uuid.UUID
    seq: int
    triage: dict | None = None
    routed_agent: str | None = None
    grounding: dict | None = None
    outcome: str | None = None
    latency_ms: int = 0
    messages: list[MessageRecord] = field(default_factory=list)
    invocations: list[InvocationRecord] = field(default_factory=list)
    citations: list[CitationRecord] = field(default_factory=list)


def _j(value) -> str | None:
    return None if value is None else json.dumps(value, default=str)


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

def create_session(
    conn: Connection,
    client: str,
    release_id: str | None,
    *,
    session_id: uuid.UUID | None = None,
    landing_url: str | None = None,
    user_agent: str | None = None,
    ip: str | None = None,
    cookie_id: str | None = None,
    locale_detected: str | None = None,
) -> uuid.UUID:
    """Insert an ``ops.session`` row (started/last_seen = now). Returns the session id."""
    sid = session_id or uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO ops.session (session_id, client_id, started_at, last_seen_at, "
            "release_id, landing_url, user_agent, ip, cookie_id, locale_detected) "
            "VALUES (:s, :c, now(), now(), :r, :lu, :ua, :ip, :ck, :loc)"
        ),
        {
            "s": sid, "c": client, "r": release_id, "lu": landing_url,
            "ua": user_agent, "ip": ip, "ck": cookie_id, "loc": locale_detected,
        },
    )
    return sid


def session_exists(conn: Connection, session_id: uuid.UUID) -> bool:
    return conn.execute(
        text("SELECT 1 FROM ops.session WHERE session_id = :s"), {"s": session_id}
    ).first() is not None


def touch_session(conn: Connection, session_id: uuid.UUID) -> None:
    """Bump ``last_seen_at`` (a designed status column — the one allowed mutation)."""
    conn.execute(
        text("UPDATE ops.session SET last_seen_at = now() WHERE session_id = :s"),
        {"s": session_id},
    )


def next_turn_seq(conn: Connection, session_id: uuid.UUID) -> int:
    current = conn.execute(
        text("SELECT max(seq) FROM ops.turn WHERE session_id = :s"), {"s": session_id}
    ).scalar_one_or_none()
    return (current or 0) + 1


# ---------------------------------------------------------------------------
# Atomic per-turn flush (FK order)
# ---------------------------------------------------------------------------

def persist_turn(conn: Connection, turn: TurnRecord) -> uuid.UUID:
    """Write a completed turn and all its children in one FK-safe insert sequence.

    Returns the turn id. No row written here is ever updated afterwards.
    """
    turn_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO ops.turn (turn_id, session_id, seq, started_at, completed_at, "
            "triage, routed_agent, grounding, outcome, latency_ms) "
            "VALUES (:t, :s, :seq, now(), now(), CAST(:tri AS jsonb), :agent, "
            "CAST(:gr AS jsonb), :out, :lat)"
        ),
        {
            "t": turn_id, "s": turn.session_id, "seq": turn.seq,
            "tri": _j(turn.triage), "agent": turn.routed_agent,
            "gr": _j(turn.grounding), "out": turn.outcome, "lat": turn.latency_ms,
        },
    )

    for i, m in enumerate(turn.messages):
        conn.execute(
            text(
                "INSERT INTO ops.message (message_id, session_id, turn_id, seq_in_turn, "
                "role, kind, text, payload, at) "
                "VALUES (:m, :s, :t, :seq, :role, :kind, :txt, CAST(:pl AS jsonb), now())"
            ),
            {
                "m": uuid.uuid4(), "s": turn.session_id, "t": turn_id, "seq": i,
                "role": m.role, "kind": m.kind, "txt": m.text, "pl": _j(m.payload),
            },
        )

    for inv in turn.invocations:
        inv_id = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO ops.agent_invocation (inv_id, turn_id, agent, prompt_id, "
                "input, output, slots, tokens_in, tokens_out, latency_ms) "
                "VALUES (:i, :t, :agent, :pid, CAST(:inp AS jsonb), CAST(:out AS jsonb), "
                "CAST(:slots AS jsonb), :tin, :tout, :lat)"
            ),
            {
                "i": inv_id, "t": turn_id, "agent": inv.agent, "pid": inv.prompt_id,
                "inp": _j(inv.input), "out": _j(inv.output), "slots": _j(inv.slots),
                "tin": inv.tokens_in, "tout": inv.tokens_out, "lat": inv.latency_ms,
            },
        )
        for tc in inv.tool_calls:
            conn.execute(
                text(
                    "INSERT INTO ops.tool_call (call_id, inv_id, tool, args, "
                    "result_summary, rows_returned, latency_ms) "
                    "VALUES (:c, :i, :tool, CAST(:args AS jsonb), "
                    "CAST(:res AS jsonb), :rows, :lat)"
                ),
                {
                    "c": uuid.uuid4(), "i": inv_id, "tool": tc.tool,
                    "args": _j(tc.args), "res": _j(tc.result),
                    "rows": tc.rows_returned, "lat": tc.latency_ms,
                },
            )

    for ct in turn.citations:
        conn.execute(
            text(
                "INSERT INTO ops.citation (citation_id, turn_id, kind, ref_id, locator, url) "
                "VALUES (:c, :t, :kind, :ref, :loc, :url)"
            ),
            {
                "c": uuid.uuid4(), "t": turn_id, "kind": ct.kind,
                "ref": ct.ref_id, "loc": ct.locator, "url": ct.url,
            },
        )
    return turn_id


# ---------------------------------------------------------------------------
# Resume: rebuild conversation context from ops rows (no checkpointer)
# ---------------------------------------------------------------------------

def rebuild_context(conn: Connection, session_id: uuid.UUID) -> list[dict]:
    """Return prior messages for a session, ordered, as ``[{role, kind, text, ...}]``.

    Sole source is the ``ops`` rows (decision-1 rider): a resumed ``chat --session <id>`` sees
    its full history without any LangGraph checkpointer.
    """
    rows = conn.execute(
        text(
            "SELECT t.seq AS turn_seq, m.seq_in_turn, m.role, m.kind, m.text, m.payload "
            "FROM ops.message m JOIN ops.turn t ON t.turn_id = m.turn_id "
            "WHERE m.session_id = :s ORDER BY t.seq, m.seq_in_turn"
        ),
        {"s": session_id},
    ).mappings().all()
    return [dict(r) for r in rows]
