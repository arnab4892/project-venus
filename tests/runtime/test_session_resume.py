"""Resuming a session sees its prior turns, rebuilt from ops rows (decision-1 rider a).

The LangGraph Postgres checkpointer is deferred (LLD-RT-02); the ``ops`` rows are the single
source of truth. ``rebuild_context`` reconstructs a session's full message history from them, and
a second turn started with that history carries it into the next triage/agent call — so
``chat --session <id>`` loses no functionality from the deferral.
"""

from __future__ import annotations

from tests.runtime._helpers import FakeLLM

from agentkit.runtime.ops import rebuild_context
from agentkit.runtime.orchestrator import run_turn


def _fake() -> FakeLLM:
    # Two turns' worth of triage/slot responses (FIFO queues).
    return FakeLLM(
        {
            "triage": [
                {"division": "industrial", "intent": "application_enquiry", "language": "en",
                 "in_scope": True, "pii_present": False, "confidence": 0.95},
                {"division": "industrial", "intent": "application_enquiry", "language": "en",
                 "in_scope": True, "pii_present": False, "confidence": 0.95},
            ],
            "application_slots": [
                # turn 1: only gas given → asks for capacity
                {"gas": "hydrogen", "capacity": None, "capacity_unit": None, "discharge_p": None,
                 "lubricated": None, "standard": None, "industry": None, "timeline": None,
                 "asked_slot": "capacity", "message": "What flow do you need?"},
                # turn 2: capacity given
                {"gas": "hydrogen", "capacity": 3000, "capacity_unit": "Nm3/hr", "discharge_p": None,
                 "lubricated": None, "standard": None, "industry": None, "timeline": None,
                 "asked_slot": "discharge_p", "message": "And the discharge pressure?"},
            ],
        }
    )


def test_resume_rebuilds_prior_turns(seeded_conn, make_ctx, new_session):
    fake = _fake()
    ctx = make_ctx(fake)
    sid = new_session()

    # Turn 1
    run_turn(ctx, sid, "I need something for hydrogen")

    # Resume: rebuild history from ops and confirm it holds turn 1's bubbles.
    history = rebuild_context(seeded_conn, sid)
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["text"] == "I need something for hydrogen"
    assert "flow" in history[1]["text"].lower()

    # Turn 2 continues with that history; a new turn row is appended (seq 2).
    result2 = run_turn(ctx, sid, "about 3000 Nm3/hr", history=history)
    assert result2.outcome == "asked_slot"

    history2 = rebuild_context(seeded_conn, sid)
    # now four bubbles across two turns, in order
    assert len(history2) == 4
    assert history2[2]["text"] == "about 3000 Nm3/hr"

    # the second turn's LLM saw the rebuilt context (its user history was threaded in)
    assert fake.seen.count("triage") == 2
