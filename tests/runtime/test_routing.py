"""Triage-intent routing (LLD-RT-03/04): all six in-scope intents land on their own agent.

``decide_route`` is pure, so this is an offline unit test. deflect is reserved for out-of-scope
(or a not-in-scope classification); low confidence clarifies (LLD-RT-04).
"""

from __future__ import annotations

import pytest

from agentkit.runtime.orchestrator import decide_route


def _t(intent, *, in_scope=True, confidence=0.9):
    return {
        "division": "industrial", "intent": intent, "language": "en",
        "in_scope": in_scope, "pii_present": False, "confidence": confidence,
    }


@pytest.mark.parametrize(
    "intent,route",
    [
        ("application_enquiry", "application_discovery"),
        ("product_question", "product_advisor"),
        ("documents", "documents_compliance"),
        ("after_sales", "after_sales_intake"),
        ("commercial", "commercial_routing"),
        ("faq", "faq_company"),
    ],
)
def test_each_intent_routes_to_its_agent(intent, route):
    got, forced = decide_route(_t(intent))
    assert got == route
    assert forced is None


def test_out_of_scope_deflects():
    assert decide_route(_t("out_of_scope")) == ("deflect", "declined_oos")


def test_not_in_scope_deflects_even_for_a_product_intent():
    assert decide_route(_t("product_question", in_scope=False)) == ("deflect", "declined_oos")


def test_low_confidence_clarifies():
    assert decide_route(_t("commercial", confidence=0.3)) == ("clarify", "clarify")
