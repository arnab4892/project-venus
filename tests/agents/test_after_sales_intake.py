"""After-Sales Intake agent (LLD-AG-04 / PRD-F-006).

Collects one slot per turn, never diagnoses, and on completion hands off naming the region's
office — with **no ``ops.lead`` row** (lead capture is milestone 6). Runs full turns on the
seeded demo release with the ``complete=`` seam.
"""

from __future__ import annotations

from tests.runtime._helpers import FakeLLM

from agentkit.runtime.orchestrator import run_turn
from sqlalchemy import text

_TRIAGE = {
    "division": "fire_rescue", "intent": "after_sales", "language": "en",
    "in_scope": True, "pii_present": False, "confidence": 0.95,
}


def _tool_names(conn, sid):
    return conn.execute(
        text(
            "SELECT tc.tool FROM ops.tool_call tc JOIN ops.agent_invocation ai "
            "ON ai.inv_id = tc.inv_id JOIN ops.turn t ON t.turn_id = ai.turn_id "
            "WHERE t.session_id = :s"
        ),
        {"s": sid},
    ).scalars().all()


def _lead_count(conn):
    return conn.execute(text("SELECT count(*) FROM ops.lead")).scalar_one()


def test_asks_one_slot_and_does_not_diagnose(seeded_conn, make_ctx, new_session):
    fake = FakeLLM(
        {
            "triage": _TRIAGE,
            # model + city known; serial/need/contact still missing → ask ONE (serial_or_year)
            "after_sales_slots": {
                "model": "MCH-16", "serial_or_year": None, "site_city": "Kolkata",
                "need": None, "contact_pref": None,
                "asked_slot": "serial_or_year",
                "message": "What's the serial number, or roughly the year it was supplied?",
            },
        }
    )
    ctx = make_ctx(fake)
    sid = new_session()
    result = run_turn(ctx, sid, "My MCH-16 breathing air compressor needs servicing, we're in Kolkata.")

    assert result.route == "after_sales_intake"
    assert result.outcome == "asked_slot"
    assert len(result.messages) == 1  # exactly one question this turn
    # no diagnosis, no premature office lookup — the intake agent has no diagnostic tools
    assert _tool_names(seeded_conn, sid) == []
    assert _lead_count(seeded_conn) == 0


def test_asks_contact_detail_after_contact_pref(seeded_conn, make_ctx, new_session):
    # Everything collected EXCEPT the actual contact detail → the agent asks for it (email/phone),
    # phrased for the chosen preference, and does not hand off yet.
    fake = FakeLLM(
        {
            "triage": _TRIAGE,
            "after_sales_slots": {
                "model": "MCH-16", "serial_or_year": "2019", "site_city": "Kolkata",
                "need": "a service visit", "contact_pref": "email", "contact_detail": None,
                "asked_slot": "contact_detail", "message": "",
            },
        }
    )
    ctx = make_ctx(fake)
    sid = new_session()
    result = run_turn(ctx, sid, "email is fine")
    assert result.outcome == "asked_slot"
    assert "email address" in result.messages[0]["text"].lower()
    assert "get_office" not in _tool_names(seeded_conn, sid)  # no handoff yet


def test_completion_hands_off_with_contact_detail_no_lead_row(seeded_conn, make_ctx, new_session):
    fake = FakeLLM(
        {
            "triage": _TRIAGE,
            "after_sales_slots": {
                "model": "MCH-16", "serial_or_year": "2019", "site_city": "Kolkata",
                "need": "a service visit", "contact_pref": "email",
                "contact_detail": "arnab@example.com",
                "asked_slot": None, "message": "",
            },
        }
    )
    ctx = make_ctx(fake)
    sid = new_session()
    result = run_turn(ctx, sid, "arnab@example.com")

    assert result.outcome == "handoff"
    # the covering office was resolved and named (Kolkata → off.kol)
    assert "get_office" in _tool_names(seeded_conn, sid)
    assert "Kolkata" in result.messages[0]["text"]
    # the collected contact detail rides the close (and the intake output → M6)
    assert "arnab@example.com" in result.messages[0]["text"]
    import json
    out = seeded_conn.execute(
        text(
            "SELECT output FROM ops.agent_invocation ai JOIN ops.turn t ON t.turn_id = ai.turn_id "
            "WHERE t.session_id = :s AND ai.agent = 'after_sales_intake'"
        ),
        {"s": sid},
    ).scalar_one()
    out = out if isinstance(out, dict) else json.loads(out)
    assert out["intake"]["contact_detail"] == "arnab@example.com"
    # 5b stub: NO ops.lead row is written (milestone 6)
    assert _lead_count(seeded_conn) == 0


def _completion_fake(language: str) -> FakeLLM:
    """A FakeLLM whose triage sets the reply language and whose slots are all filled (so the turn
    reaches the completion close)."""
    return FakeLLM(
        {
            "triage": {**_TRIAGE, "language": language},
            "after_sales_slots": {
                "model": "MCH-16", "serial_or_year": "2019", "site_city": "Kolkata",
                "need": "a service visit", "contact_pref": "email",
                "contact_detail": "arnab@example.com",
                "asked_slot": None, "message": "",
            },
        }
    )


def test_completion_close_english(seeded_conn, make_ctx, new_session):
    ctx = make_ctx(_completion_fake("en"))
    result = run_turn(ctx, new_session(), "arnab@example.com")
    text = result.messages[0]["text"]
    assert result.outcome == "handoff"
    assert text.startswith("Thanks")
    # office named, and the visitor's own values ride the close verbatim
    assert "Kolkata" in text and "MCH-16" in text
    assert "a service visit" in text and "arnab@example.com" in text


def test_completion_close_hinglish(seeded_conn, make_ctx, new_session):
    ctx = make_ctx(_completion_fake("hinglish"))
    result = run_turn(ctx, new_session(), "arnab@example.com")
    text = result.messages[0]["text"]
    assert result.outcome == "handoff"
    # Hinglish scaffolding (Latin script), technical/visitor values verbatim
    assert "Shukriya" in text and "contact karenge" in text
    assert "Kolkata" in text and "MCH-16" in text and "arnab@example.com" in text


def test_completion_close_hindi(seeded_conn, make_ctx, new_session):
    ctx = make_ctx(_completion_fake("hi"))
    result = run_turn(ctx, new_session(), "arnab@example.com")
    text = result.messages[0]["text"]
    assert result.outcome == "handoff"
    # Devanagari scaffolding present; visitor/technical values stay verbatim in Latin
    assert "धन्यवाद" in text and "संपर्क करेंगे" in text
    assert "Kolkata" in text and "MCH-16" in text and "arnab@example.com" in text


def test_hi_close_template_is_register_pure():
    """The hi completion close is a fixed Devanagari template — its scaffolding must be
    register-pure (LLD-RT-07), verified with the same checker the eval gate uses. The checker
    strips `{placeholders}`, so the runtime-filled technical values (model/city/need/contact/
    office email) are correctly excluded — only the fixed Hindi prose is asserted pure."""
    from agentkit.eval.runner import script_purity_offenders
    from agentkit.runtime.agents.after_sales_intake import _CLOSE_TEMPLATES

    assert script_purity_offenders(_CLOSE_TEMPLATES["hi"]) == []


def test_ask_slot_strips_empty_sources_trailer(seeded_conn, make_ctx, new_session):
    # Same prompt-hygiene net as application_discovery: a spurious empty "Sources:" trailer on a
    # slot question is stripped (a question has no citations).
    fake = FakeLLM(
        {
            "triage": _TRIAGE,
            "after_sales_slots": {
                "model": "MCH-16", "serial_or_year": None, "site_city": "Kolkata",
                "need": None, "contact_pref": None,
                "asked_slot": "serial_or_year",
                "message": "What's the serial number, or roughly the year it was supplied?\n\nSources: []",
            },
        }
    )
    ctx = make_ctx(fake)
    sid = new_session()
    result = run_turn(ctx, sid, "My MCH-16 needs servicing, we're in Kolkata.")

    assert result.outcome == "asked_slot"
    text = result.messages[0]["text"]
    assert "Sources" not in text
    assert "serial number" in text.lower()
