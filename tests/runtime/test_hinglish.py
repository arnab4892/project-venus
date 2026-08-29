"""Hinglish handling (PRD-F-012 / LLD-RT-07).

A romanised Hindi/English message is routed like its English equivalent and the detected
language is tagged on the turn (``ops.turn.triage.language == 'hinglish'``). Runs a full turn on
the seeded DB with the ``complete=`` seam so the routing + persistence path is exercised end to
end. This file is the one TRACEABILITY names for PRD-F-012.
"""

from __future__ import annotations

import json

from tests.runtime._helpers import FakeLLM

from agentkit.runtime.orchestrator import run_turn
from sqlalchemy import text

HINGLISH = "Hydrogen compressor chahiye, 3000 Nm3/hr, oil-free"


def _fake() -> FakeLLM:
    return FakeLLM(
        {
            "triage": {
                "division": "industrial",
                "intent": "application_enquiry",
                "language": "hinglish",
                "in_scope": True,
                "pii_present": False,
                "confidence": 0.9,
            },
            # discharge pressure not given → the agent should ask exactly one slot.
            "application_slots": {
                "gas": "hydrogen",
                "capacity": 3000,
                "capacity_unit": "Nm3/hr",
                "discharge_p": None,
                "lubricated": False,
                "standard": None,
                "industry": None,
                "timeline": None,
                "asked_slot": "discharge_p",
                "message": "Aapko kitna discharge pressure chahiye (bar mein)?",
            },
        }
    )


def test_hinglish_routed_and_language_tagged(seeded_conn, make_ctx, new_session):
    ctx = make_ctx(_fake())
    sid = new_session()
    result = run_turn(ctx, sid, HINGLISH)

    # routed like the English application enquiry; asked one slot (discharge_p)
    assert result.route == "application_discovery"
    assert result.outcome == "asked_slot"

    # language tagged on the persisted turn
    triage = seeded_conn.execute(
        text("SELECT triage FROM ops.turn WHERE session_id = :s"), {"s": sid}
    ).scalar_one()
    triage = triage if isinstance(triage, dict) else json.loads(triage)
    assert triage["language"] == "hinglish"
