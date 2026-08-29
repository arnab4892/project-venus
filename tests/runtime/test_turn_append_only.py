"""A turn writes ops.* append-only (LLD-RT-06 / data-model §3).

The runtime must INSERT conversation rows and never UPDATE/DELETE them (the only sanctioned
mutations are the designed status columns — none touched during a plain turn). We attach a SQL
event listener to the connection, run a full turn, and assert every statement issued against an
``ops.<conversation table>`` is an INSERT — no UPDATE/DELETE on turn/message/agent_invocation/
tool_call/citation. (SAVEPOINT/RELEASE from the tool wrapper are fine; they are not row writes.)
"""

from __future__ import annotations

import re

from tests.runtime._helpers import FakeLLM

from agentkit.runtime.orchestrator import run_turn
from sqlalchemy import event

_CONV_TABLES = ("turn", "message", "agent_invocation", "tool_call", "citation")
_MUTATE_RE = re.compile(
    r"\b(update|delete)\b[\s\S]*\bops\.(turn|message|agent_invocation|tool_call|citation)\b",
    re.IGNORECASE,
)


def _fake() -> FakeLLM:
    return FakeLLM(
        {
            "triage": {
                "division": "industrial", "intent": "application_enquiry", "language": "en",
                "in_scope": True, "pii_present": False, "confidence": 0.95,
            },
            "application_slots": {
                "gas": "hydrogen", "capacity": 3000, "capacity_unit": "Nm3/hr", "discharge_p": 350,
                "lubricated": False, "standard": None, "industry": None, "timeline": None,
                "asked_slot": None, "message": "",
            },
            "answer": {
                "message": "That duty (3000 Nm3/hr at 350 bar) sits in our process range [tr1].",
                "citations": ["tr1"],
            },
        }
    )


def test_turn_issues_only_inserts_on_conversation_tables(seeded_conn, make_ctx, new_session):
    statements: list[str] = []

    @event.listens_for(seeded_conn, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    try:
        ctx = make_ctx(_fake())
        sid = new_session()
        run_turn(ctx, sid, "Hydrogen, 3000 Nm3/hr at 350 bar, oil-free")
    finally:
        event.remove(seeded_conn, "before_cursor_execute", _capture)

    offending = [s for s in statements if _MUTATE_RE.search(s)]
    assert not offending, f"conversation tables must be append-only; found: {offending}"

    # sanity: the turn DID insert into every conversation table
    inserts = " ".join(statements).lower()
    for tbl in _CONV_TABLES:
        assert f"insert into ops.{tbl}" in inserts, f"expected an insert into ops.{tbl}"
