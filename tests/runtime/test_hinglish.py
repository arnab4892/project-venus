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


def test_response_language_is_current_turn_not_history():
    # An English turn following Hinglish turns in the same session must be answered in English:
    # the compose carries the CURRENT turn's language instruction, emphatically, so it wins over
    # the language of earlier messages (LLD-RT-07). And vice-versa for a Hinglish turn.
    import json as _json

    from agentkit.runtime.agents.base import compose_grounded_answer

    captured = {}

    def _capture(messages, json_schema, schema_name):
        captured["system"] = messages[0]["content"]
        return _json.dumps({"message": "ok", "citations": []})

    hinglish_history = [{"role": "assistant", "kind": "text", "text": "Aapke liye yeh sahi hai."}]
    compose_grounded_answer(
        complete=_capture, prompt_body="P", history=hinglish_history,
        latest_user="What is the maximum capacity?", records=[], language="en",
    )
    assert "Respond in English." in captured["system"]
    assert "regardless of the language used in earlier messages" in captured["system"]

    compose_grounded_answer(
        complete=_capture, prompt_body="P", history=[], latest_user="capacity kya hai?",
        records=[], language="hinglish",
    )
    assert "Hinglish" in captured["system"]
    assert "regardless of the language used in earlier messages" in captured["system"]


def _full_slot_fake() -> FakeLLM:
    return FakeLLM(
        {
            "triage": {
                "division": "industrial", "intent": "application_enquiry", "language": "hinglish",
                "in_scope": True, "pii_present": False, "confidence": 0.92,
            },
            # complete duty → match_capability runs; reply composed in Hinglish
            "application_slots": {
                "gas": "hydrogen", "capacity": 3000, "capacity_unit": "Nm3/hr", "discharge_p": 350,
                "lubricated": False, "standard": None, "industry": None, "timeline": None,
                "asked_slot": None, "message": "",
            },
            "answer": {
                "message": "Aapki hydrogen duty hamari Process Gas Compressor range mein "
                           "comfortably fit hoti hai [tr1].",
                "citations": ["tr1"],
            },
        }
    )


def test_hinglish_full_slot_answered_in_hinglish_with_citation(seeded_conn, make_ctx, new_session):
    # A complete Hinglish capability turn is routed, matched, answered in Hinglish, and cites the
    # capability match (LLD-RT-07 + same citation discipline as the English path).
    ctx = make_ctx(_full_slot_fake())
    sid = new_session()
    result = run_turn(ctx, sid, "Hydrogen compressor chahiye, 3000 Nm3/hr, 350 bar tak, oil-free")

    assert result.route == "application_discovery"
    assert result.outcome == "answered"
    assert result.grounding["status"] == "full"
    # citation discipline: the matched capability row + its family are cited
    kinds = {c["kind"] for c in result.citations}
    assert "capability" in kinds and "family" in kinds
    # language stayed Hinglish on the turn
    triage = seeded_conn.execute(
        text("SELECT triage FROM ops.turn WHERE session_id = :s"), {"s": sid}
    ).scalar_one()
    triage = triage if isinstance(triage, dict) else json.loads(triage)
    assert triage["language"] == "hinglish"
