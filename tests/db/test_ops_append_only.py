"""LLD-DB-04 / data-model §3 — the ops.* schema accepts the runtime's inserts.

A focused round-trip: insert one row into every ``ops.*`` table in dependency order
(client → session → turn → message / agent_invocation → tool_call → citation;
lead → handoff_dispatch), read them back, and assert the ``ops.message`` kind check
constraint rejects an out-of-vocabulary kind. Everything runs inside a rolled-back
transaction on the shared ``engine`` fixture, so no rows persist.

This proves the append-only conversational store is shippable end to end; the
orchestrator's insert-only writers (``runtime/ops.py``) build on exactly these tables.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError


@pytest.fixture()
def ops_conn(engine: Engine):
    conn = engine.connect()
    trans = conn.begin()
    try:
        yield conn
    finally:
        trans.rollback()
        conn.close()


def test_ops_full_insert_roundtrip(ops_conn):
    c = ops_conn
    cid = "__ops_test__"
    c.execute(
        text("INSERT INTO ops.client(client_id, name, active_release) VALUES (:c, :n, :r)"),
        {"c": cid, "n": "Test Co", "r": "r2026.08.2"},
    )
    pid = "pv.test.triage.1"
    c.execute(
        text(
            "INSERT INTO ops.prompt_version(prompt_id, client_id, agent, version, is_active, body) "
            "VALUES (:p, :c, 'triage', 1, true, 'classify…')"
        ),
        {"p": pid, "c": cid},
    )
    sid = uuid.uuid4()
    c.execute(
        text("INSERT INTO ops.session(session_id, client_id, release_id) VALUES (:s, :c, :r)"),
        {"s": sid, "c": cid, "r": "r2026.08.2"},
    )
    tid = uuid.uuid4()
    c.execute(
        text("INSERT INTO ops.turn(turn_id, session_id, seq, outcome) VALUES (:t, :s, 1, 'answered')"),
        {"t": tid, "s": sid},
    )
    c.execute(
        text(
            "INSERT INTO ops.message(message_id, session_id, turn_id, seq_in_turn, role, kind, text) "
            "VALUES (:m, :s, :t, 0, 'user', 'text', 'hi')"
        ),
        {"m": uuid.uuid4(), "s": sid, "t": tid},
    )
    inv = uuid.uuid4()
    c.execute(
        text(
            "INSERT INTO ops.agent_invocation(inv_id, turn_id, agent, prompt_id) "
            "VALUES (:i, :t, 'triage', :p)"
        ),
        {"i": inv, "t": tid, "p": pid},
    )
    c.execute(
        text(
            "INSERT INTO ops.tool_call(call_id, inv_id, tool, rows_returned) "
            "VALUES (:c, :i, 'match_capability', 1)"
        ),
        {"c": uuid.uuid4(), "i": inv},
    )
    c.execute(
        text(
            "INSERT INTO ops.citation(citation_id, turn_id, kind, ref_id, locator, url) "
            "VALUES (:c, :t, 'capability', 'cap.002', '§Process', 'https://x')"
        ),
        {"c": uuid.uuid4(), "t": tid},
    )
    lid = uuid.uuid4()
    c.execute(
        text("INSERT INTO ops.lead(lead_id, session_id, status) VALUES (:l, :s, 'new')"),
        {"l": lid, "s": sid},
    )
    # 'to' is a SQL reserved word — the schema (and the driver) must quote it.
    c.execute(
        text(
            'INSERT INTO ops.handoff_dispatch(dispatch_id, lead_id, "to", status) '
            "VALUES (:d, :l, 'sales@jyotech.com', 'sent')"
        ),
        {"d": uuid.uuid4(), "l": lid},
    )

    # read-back spot checks
    assert c.execute(
        text("SELECT count(*) FROM ops.message WHERE turn_id = :t"), {"t": tid}
    ).scalar_one() == 1
    assert c.execute(
        text('SELECT "to" FROM ops.handoff_dispatch WHERE lead_id = :l'), {"l": lid}
    ).scalar_one() == "sales@jyotech.com"
    assert c.execute(
        text("SELECT prompt_id FROM ops.agent_invocation WHERE inv_id = :i"), {"i": inv}
    ).scalar_one() == pid


def test_message_kind_check_constraint(ops_conn):
    c = ops_conn
    cid = "__ops_test2__"
    c.execute(text("INSERT INTO ops.client(client_id) VALUES (:c)"), {"c": cid})
    sid = uuid.uuid4()
    c.execute(
        text("INSERT INTO ops.session(session_id, client_id) VALUES (:s, :c)"),
        {"s": sid, "c": cid},
    )
    tid = uuid.uuid4()
    c.execute(
        text("INSERT INTO ops.turn(turn_id, session_id) VALUES (:t, :s)"),
        {"t": tid, "s": sid},
    )
    with pytest.raises(IntegrityError):
        c.execute(
            text(
                "INSERT INTO ops.message(message_id, session_id, turn_id, seq_in_turn, role, kind) "
                "VALUES (:m, :s, :t, 0, 'assistant', 'bogus')"
            ),
            {"m": uuid.uuid4(), "s": sid, "t": tid},
        )


def test_one_active_prompt_version_per_agent(ops_conn):
    """The partial unique index enforces one active prompt version per (client, agent)."""
    c = ops_conn
    cid = "__ops_test3__"
    c.execute(text("INSERT INTO ops.client(client_id) VALUES (:c)"), {"c": cid})
    c.execute(
        text(
            "INSERT INTO ops.prompt_version(prompt_id, client_id, agent, version, is_active) "
            "VALUES ('pv.a', :c, 'triage', 1, true)"
        ),
        {"c": cid},
    )
    with pytest.raises(IntegrityError):
        c.execute(
            text(
                "INSERT INTO ops.prompt_version(prompt_id, client_id, agent, version, is_active) "
                "VALUES ('pv.b', :c, 'triage', 2, true)"
            ),
            {"c": cid},
        )
