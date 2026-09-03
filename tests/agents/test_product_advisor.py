"""Product Advisor agent (LLD-AG-02 / PRD-F-004).

Exact model names from the tool result; a null-``model_name`` product presented by family +
variant (LLD-EXT-06). Runs full turns on the seeded demo release with the ``complete=`` seam.
"""

from __future__ import annotations

import json

from tests.runtime._helpers import FakeLLM

from agentkit.config import Settings
from agentkit.retrieval.chunk import Chunk
from agentkit.retrieval.embed import embed_chunks
from agentkit.runtime.orchestrator import run_turn
from sqlalchemy import text

_SETTINGS = Settings(embed_dim=3, embed_batch=64, embed_model="bge-m3")


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


def test_get_product_family_force_cited_when_compose_cites_only_chunk(
    seeded_conn, make_ctx, new_session
):
    # The get_product row + its family must be grounded even when the compose LLM cites ONLY the
    # supporting chunk (never the structured row) — product_advisor force-cites the get_product
    # record, mirroring application_discovery's match force-cite (LLD-AG-02).
    fam = seeded_conn.execute(text(
        "SELECT family_id FROM facts.product WHERE release_id='r2026.08.1' AND product_id='prd.mch6'"
    )).scalar_one()
    doc_id = seeded_conn.execute(text(
        "SELECT doc_id FROM facts.document WHERE release_id='r2026.08.1' AND doc_id='doc.fs'"
    )).scalar_one()
    embed_chunks(
        seeded_conn,
        [Chunk("ch.mch.0", doc_id, "§MCH", ["MCH"],
               "MCH-6 portable breathing air compressor", 8, [fam], "mch")],
        "r2026.08.1", embed=lambda t: [[1.0, 0.0, 0.0] for _ in t], settings=_SETTINGS,
    )
    fake = FakeLLM({
        "triage": {"division": "fire_rescue", "intent": "product_question", "language": "en",
                   "in_scope": True, "pii_present": False, "confidence": 0.95},
        "product_query": {"model_or_family": "MCH-6", "is_price_or_leadtime": False,
                          "search_query": "MCH-6 breathing air compressor"},
        # cites ONLY the search chunk (tr2), never the get_product row (tr1)
        "answer": {"message": "The MCH-6 is a portable breathing air compressor [tr2].",
                   "citations": ["tr2"]},
    })
    ctx = make_ctx(fake, embed=lambda t: [[1.0, 0.0, 0.0]], settings=_SETTINGS)
    sid = new_session()
    result = run_turn(ctx, sid, "Tell me about the MCH-6.")

    assert result.outcome == "answered"
    kinds = {c["kind"] for c in result.citations}
    # product + family come from the force-cited get_product row, not the compose citation
    assert "product" in kinds and "family" in kinds


def test_unresolved_name_falls_back_to_list_products_for_family_citation(
    seeded_conn, make_ctx, new_session
):
    # cap-hydrogen-fuelling flake: a family name get_product cannot resolve (a family with no
    # product rows) must fall back to the division listing so the family is still grounded — not
    # left to the compose LLM to remember. Here compose cites nothing; the family is force-cited
    # from the list_products fallback.
    fake = FakeLLM({
        "triage": {"division": "industrial", "intent": "product_question", "language": "en",
                   "in_scope": True, "pii_present": False, "confidence": 0.95},
        "product_query": {"model_or_family": "Zzz Nonexistent Range 9000",
                          "is_price_or_leadtime": False, "search_query": "nonexistent range"},
        "answer": {"message": "We make a range of industrial compressors.", "citations": []},
    })
    ctx = make_ctx(fake)
    sid = new_session()
    result = run_turn(ctx, sid, "Tell me about your industrial range.")

    assert result.outcome == "answered"
    kinds = {c["kind"] for c in result.citations}
    assert "family" in kinds  # force-cited from the list_products fallback, despite an empty compose cite


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


def test_price_ask_hands_off_in_visitor_language(seeded_conn, make_ctx, new_session):
    """A Hindi price ask hands off in Hindi, still with no figure (LLD-RT-07 + LLD-AG-05)."""
    from agentkit.runtime.agents.product_advisor import _PRICE_HANDOFF_TEXTS

    fake = FakeLLM(
        {
            "triage": {
                "division": "fire_rescue", "intent": "product_question", "language": "hi",
                "in_scope": True, "pii_present": False, "confidence": 0.9,
            },
            "product_query": {
                "model_or_family": "MCH-6", "is_price_or_leadtime": True, "search_query": "MCH-6 price",
            },
        }
    )
    ctx = make_ctx(fake)
    sid = new_session()
    result = run_turn(ctx, sid, "MCH-6 ki keemat kya hai?")
    assert result.outcome == "handoff"
    assert result.messages[0]["text"] == _PRICE_HANDOFF_TEXTS["hi"]
    assert not any(ch.isdigit() for ch in result.messages[0]["text"])
