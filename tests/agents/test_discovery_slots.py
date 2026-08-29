"""Application Discovery slot-filling (LLD-AG-01 / PRD-F-002).

The agent asks exactly ONE missing slot per turn and never invents a value: given a message with
only the gas, it must ask for capacity and must NOT call ``match_capability`` (no complete duty
to match). When all three required slots are present it calls the tool. Runs full turns on the
seeded DB with the ``complete=`` seam.
"""

from __future__ import annotations

from tests.runtime._helpers import FakeLLM

from agentkit.runtime.orchestrator import run_turn
from sqlalchemy import text


def _partial_fake() -> FakeLLM:
    return FakeLLM(
        {
            "triage": {
                "division": "industrial", "intent": "application_enquiry", "language": "en",
                "in_scope": True, "pii_present": False, "confidence": 0.95,
            },
            # only the gas is known; capacity + discharge_p missing
            "application_slots": {
                "gas": "hydrogen", "capacity": None, "capacity_unit": None, "discharge_p": None,
                "lubricated": None, "standard": None, "industry": None, "timeline": None,
                "asked_slot": "capacity", "message": "What flow/capacity do you need (with units)?",
            },
        }
    )


def _tool_names(conn, sid) -> list[str]:
    return conn.execute(
        text(
            "SELECT tc.tool FROM ops.tool_call tc JOIN ops.agent_invocation ai "
            "ON ai.inv_id = tc.inv_id JOIN ops.turn t ON t.turn_id = ai.turn_id "
            "WHERE t.session_id = :s"
        ),
        {"s": sid},
    ).scalars().all()


def test_asks_one_slot_and_calls_no_tool_when_incomplete(seeded_conn, make_ctx, new_session):
    ctx = make_ctx(_partial_fake())
    sid = new_session()
    result = run_turn(ctx, sid, "I need something for hydrogen")

    assert result.outcome == "asked_slot"
    assert len(result.messages) == 1  # exactly one question bubble
    # never guessed a duty → match_capability was NOT called
    assert _tool_names(seeded_conn, sid) == []
    # the persisted invocation recorded the asked slot, and did not invent capacity/pressure
    slots = seeded_conn.execute(
        text(
            "SELECT slots FROM ops.agent_invocation WHERE agent = 'application_discovery' "
            "AND turn_id = (SELECT turn_id FROM ops.turn WHERE session_id = :s)"
        ),
        {"s": sid},
    ).scalar_one()
    import json

    slots = slots if isinstance(slots, dict) else json.loads(slots)
    assert slots.get("gas") == "hydrogen"
    assert "capacity" not in slots and "discharge_p" not in slots


def _complete_fake() -> FakeLLM:
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


def test_calls_match_capability_when_slots_complete(seeded_conn, make_ctx, new_session):
    ctx = make_ctx(_complete_fake())
    sid = new_session()
    result = run_turn(ctx, sid, "hydrogen, 3000 Nm3/hr, 350 bar, oil-free")
    assert result.outcome == "answered"
    assert "match_capability" in _tool_names(seeded_conn, sid)
