"""Deflect path for out-of-scope messages (LLD-AG deflect / PRD-F-013).

"What's the weather in Mumbai?" is out of scope: the turn is deflected with a polite scope
statement, makes no tool calls, cites nothing — but is still recorded in ops (outcome
``declined_oos``). Runs a full turn on the seeded DB.
"""

from __future__ import annotations

from tests.runtime._helpers import FakeLLM

from agentkit.runtime.orchestrator import run_turn
from sqlalchemy import text

SCOPE_MSG = (
    "I can help with Jyotech's compressors and process equipment, fire/rescue/diving safety "
    "equipment, company information and documents, or connect you with our team."
)


def _fake() -> FakeLLM:
    return FakeLLM(
        {
            "triage": {
                "division": "unknown", "intent": "out_of_scope", "language": "en",
                "in_scope": False, "pii_present": False, "confidence": 0.98,
            },
            "deflect": {"message": SCOPE_MSG},
        }
    )


def test_out_of_scope_is_deflected_and_recorded(seeded_conn, make_ctx, new_session):
    ctx = make_ctx(_fake())
    sid = new_session()
    result = run_turn(ctx, sid, "What's the weather in Mumbai today?")

    assert result.route == "deflect"
    assert result.outcome == "declined_oos"
    assert result.messages[-1]["text"] == SCOPE_MSG
    assert result.citations == []

    # recorded in ops: one turn routed to deflect, no tool calls, no citations
    row = seeded_conn.execute(
        text("SELECT routed_agent, outcome FROM ops.turn WHERE session_id = :s"), {"s": sid}
    ).mappings().one()
    assert row["routed_agent"] == "deflect"
    assert row["outcome"] == "declined_oos"
    assert seeded_conn.execute(
        text(
            "SELECT count(*) FROM ops.tool_call tc JOIN ops.agent_invocation ai "
            "ON ai.inv_id = tc.inv_id JOIN ops.turn t ON t.turn_id = ai.turn_id "
            "WHERE t.session_id = :s"
        ),
        {"s": sid},
    ).scalar_one() == 0
