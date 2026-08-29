"""Triage classification + routing (LLD-RT-04 / PRD-F-001).

Pure-Python: ``run_triage`` goes through the injected ``complete=`` seam and needs no DB. Checks
the coercion of division/intent/language/in_scope, the confidence→clarify threshold, and the
intent→route mapping (``decide_route``).
"""

from __future__ import annotations

import json

from agentkit.runtime.orchestrator import decide_route
from agentkit.runtime.triage import needs_clarification, run_triage


def _complete_for(payload: dict):
    def _complete(messages, schema, name):
        return json.dumps(payload)

    return _complete


def _triage(payload: dict) -> dict:
    return run_triage(
        complete=_complete_for(payload),
        prompt_body="classify",
        history=[],
        latest_user="…",
    )


def test_application_enquiry_industrial():
    t = _triage(
        {
            "division": "industrial",
            "intent": "application_enquiry",
            "language": "en",
            "in_scope": True,
            "pii_present": False,
            "confidence": 0.95,
        }
    )
    assert t["division"] == "industrial"
    assert t["intent"] == "application_enquiry"
    assert t["language"] == "en"
    assert t["in_scope"] is True
    assert decide_route(t) == ("application_discovery", None)


def test_faq_routes_to_faq_company():
    t = _triage(
        {
            "division": "unknown",
            "intent": "faq",
            "language": "en",
            "in_scope": True,
            "pii_present": False,
            "confidence": 0.9,
        }
    )
    assert decide_route(t) == ("faq_company", None)


def test_out_of_scope_forces_in_scope_false_and_deflects():
    t = _triage(
        {
            "division": "unknown",
            "intent": "out_of_scope",
            "language": "en",
            "in_scope": True,  # model said true, but out_of_scope must override
            "pii_present": False,
            "confidence": 0.99,
        }
    )
    assert t["in_scope"] is False
    assert decide_route(t) == ("deflect", "declined_oos")


def test_deferred_intents_hold_via_deflect():
    for intent in ("product_question", "after_sales", "commercial"):
        t = _triage(
            {
                "division": "fire_rescue",
                "intent": intent,
                "language": "en",
                "in_scope": True,
                "pii_present": False,
                "confidence": 0.9,
            }
        )
        assert decide_route(t) == ("deflect", "deflected")


def test_bad_enum_values_coerced():
    t = _triage(
        {
            "division": "martian",
            "intent": "nonsense",
            "language": "kl",
            "in_scope": True,
            "pii_present": False,
            "confidence": 2.5,
        }
    )
    assert t["division"] == "unknown"
    assert t["intent"] == "faq"  # default fallback intent
    assert t["language"] == "en"
    assert t["confidence"] == 1.0  # clamped to [0, 1]


def test_low_confidence_triggers_clarify():
    t = _triage(
        {
            "division": "industrial",
            "intent": "application_enquiry",
            "language": "en",
            "in_scope": True,
            "pii_present": False,
            "confidence": 0.4,
        }
    )
    assert needs_clarification(t)
    assert decide_route(t) == ("clarify", "clarify")
