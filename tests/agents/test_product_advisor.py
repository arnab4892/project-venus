"""Product Advisor agent (LLD-AG-02 / PRD-F-004).

Exact model names from the tool result; a null-``model_name`` product presented by family +
variant (LLD-EXT-06). Runs full turns on the seeded demo release with the ``complete=`` seam.
"""

from __future__ import annotations

import json

from tests.runtime._helpers import FakeLLM

from agentkit.runtime.orchestrator import run_turn
from sqlalchemy import text


def _tool_results(conn, sid, tool):
    rows = conn.execute(
        text(
            "SELECT tc.result_summary FROM ops.tool_call tc JOIN ops.agent_invocation ai "
            "ON ai.inv_id = tc.inv_id JOIN ops.turn t ON t.turn_id = ai.turn_id "
            "WHERE t.session_id = :s AND tc.tool = :tool"
        ),
        {"s": sid, "tool": tool},
    ).scalars().all()
    return [r if isinstance(r, dict) else json.loads(r) for r in rows]


def _fake(model_or_family: str) -> FakeLLM:
    return FakeLLM(
        {
            "triage": {
                "division": "fire_rescue", "intent": "product_question", "language": "en",
                "in_scope": True, "pii_present": False, "confidence": 0.95,
            },
            "product_query": {
                "model_or_family": model_or_family,
                "is_price_or_leadtime": False,
                "search_query": "MCH breathing air compressor",
            },
            "answer": {
                "message": "The MCH-6 is a portable breathing air compressor [tr1].",
                "citations": ["tr1"],
            },
        }
    )


def test_exact_model_name_and_family_citation(seeded_conn, make_ctx, new_session):
    ctx = make_ctx(_fake("MCH-6"))
    sid = new_session()
    result = run_turn(ctx, sid, "Tell me about the MCH-6.")

    assert result.route == "product_advisor"
    assert result.outcome == "answered"
    assert "MCH-6" in result.messages[0]["text"]
    # get_product returned the exact model name (not blank, not invented)
    prods = _tool_results(seeded_conn, sid, "get_product")[0]["products"]
    assert any(p["display_name"] == "MCH-6" for p in prods)
    # citations carry the product's parent family (grounding parent-derivation)
    kinds = {c["kind"] for c in result.citations}
    assert "family" in kinds and "product" in kinds


def _null_fake() -> FakeLLM:
    return FakeLLM(
        {
            "triage": {
                "division": "industrial", "intent": "product_question", "language": "en",
                "in_scope": True, "pii_present": False, "confidence": 0.95,
            },
            "product_query": {
                "model_or_family": "fam.diaphragm",
                "is_price_or_leadtime": False,
                "search_query": "diaphragm compressor",
            },
            "answer": {
                "message": "We make Diaphragm Compressors, including a high-pressure variant [tr1].",
                "citations": ["tr1"],
            },
        }
    )


def test_null_model_presented_by_family_and_variant(seeded_conn, make_ctx, new_session):
    # Insert a name-less product under the diaphragm family (LLD-EXT-06 display rule).
    seeded_conn.execute(text(
        "INSERT INTO facts.product (product_id, release_id, family_id, model_name, variant, "
        " description, attributes, source_doc_id, source_locator) "
        "SELECT 'prd.noname', 'r2026.08.1', 'fam.diaphragm', NULL, 'high-pressure', NULL, "
        " '{}'::jsonb, source_doc_id, source_locator "
        "FROM facts.product WHERE release_id='r2026.08.1' AND product_id='prd.mch6'"
    ))
    ctx = make_ctx(_null_fake())
    sid = new_session()
    result = run_turn(ctx, sid, "What diaphragm compressors do you have?")

    assert result.outcome == "answered"
    # the null-model product was surfaced by its family name + variant, never blank/invented
    prods = _tool_results(seeded_conn, sid, "get_product")[0]["products"]
    names = [p["display_name"] for p in prods]
    assert "Diaphragm Compressor — high-pressure" in names
    assert "" not in names and None not in names


def test_price_ask_routes_to_handoff_no_number(seeded_conn, make_ctx, new_session):
    fake = FakeLLM(
        {
            "triage": {
                "division": "fire_rescue", "intent": "product_question", "language": "en",
                "in_scope": True, "pii_present": False, "confidence": 0.9,
            },
            "product_query": {
                "model_or_family": "MCH-6",
                "is_price_or_leadtime": True,
                "search_query": "MCH-6 price",
            },
        }
    )
    ctx = make_ctx(fake)
    sid = new_session()
    result = run_turn(ctx, sid, "What does the MCH-6 cost?")
    assert result.outcome == "handoff"
    assert not any(ch.isdigit() for ch in result.messages[0]["text"])
