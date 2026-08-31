"""Commercial Routing agent (LLD-AG-05 / PRD-F-007).

Always hands off with ``lead_type ∈ {commercial, dealer}`` and never states a price or lead
time — the reply carries no figures at all (the guarantee holds by construction, not via the
numeric-guard backstop). Replies in the visitor's language (Hinglish stays Hinglish).
"""

from __future__ import annotations

from tests.runtime._helpers import FakeLLM

from agentkit.runtime.orchestrator import run_turn
from sqlalchemy import text


def _triage(language: str, **over):
    base = {
        "division": "fire_rescue", "intent": "commercial", "language": language,
        "in_scope": True, "pii_present": False, "confidence": 0.95,
    }
    base.update(over)
    return base


def _outcome(conn, sid):
    return conn.execute(
        text("SELECT outcome, routed_agent FROM ops.turn WHERE session_id = :s"), {"s": sid}
    ).mappings().one()


def test_price_ask_always_hands_off_without_a_number(seeded_conn, make_ctx, new_session):
    ctx = make_ctx(FakeLLM({"triage": _triage("en")}))
    sid = new_session()
    result = run_turn(ctx, sid, "How much does the MCH-16 cost?")

    assert result.route == "commercial_routing"
    assert result.outcome == "handoff"
    msg = result.messages[0]["text"]
    assert not any(ch.isdigit() for ch in msg)  # no price / lead-time figure, ever
    assert "₹" not in msg and "$" not in msg
    row = _outcome(seeded_conn, sid)
    assert row["outcome"] == "handoff" and row["routed_agent"] == "commercial_routing"


def test_hinglish_price_ask_answered_in_hinglish_no_number(seeded_conn, make_ctx, new_session):
    ctx = make_ctx(FakeLLM({"triage": _triage("hinglish")}))
    sid = new_session()
    result = run_turn(ctx, sid, "MCH-16 ka price kya hai?")

    assert result.outcome == "handoff"
    msg = result.messages[0]["text"]
    # Hinglish (Latin-script) reply with no numbers
    assert "connect" in msg.lower() or "jodun" in msg.lower()
    assert not any(ch.isdigit() for ch in msg)


def test_dealer_ask_routes_as_dealer(seeded_conn, make_ctx, new_session):
    ctx = make_ctx(FakeLLM({"triage": _triage("en")}))
    sid = new_session()
    result = run_turn(ctx, sid, "Do you have a distributor in Chennai?")
    assert result.outcome == "handoff"
    # lead_type recorded on the invocation output
    out = seeded_conn.execute(
        text(
            "SELECT output FROM ops.agent_invocation ai JOIN ops.turn t ON t.turn_id = ai.turn_id "
            "WHERE t.session_id = :s AND ai.agent = 'commercial_routing'"
        ),
        {"s": sid},
    ).scalar_one()
    import json
    out = out if isinstance(out, dict) else json.loads(out)
    assert out["lead_type"] == "dealer"
