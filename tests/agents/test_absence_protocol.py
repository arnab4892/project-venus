"""Absence protocol (LLD-AG): no agent may concede "we don't have that" for a factual question
until search_documents has actually looked. A first draft that reads as an absence claim, with no
retrieval yet this turn, triggers a search + one recompose over the enlarged evidence.

The compose LLM is scripted to return an ABSENCE draft first, then a real answer; the test asserts
search_documents ran (before the final answer) and the concession was replaced by the hit.
"""

from __future__ import annotations

from tests.runtime._helpers import FakeLLM

from agentkit.config import Settings
from agentkit.retrieval.chunk import Chunk
from agentkit.retrieval.embed import embed_chunks
from agentkit.runtime.orchestrator import run_turn
from sqlalchemy import text

_SETTINGS = Settings(embed_dim=3, embed_batch=64, embed_model="bge-m3")
_VEC = lambda t: [[1.0, 0.0, 0.0]]  # noqa: E731


def _tool_names(conn, sid):
    return conn.execute(
        text(
            "SELECT tc.tool FROM ops.tool_call tc JOIN ops.agent_invocation ai "
            "ON ai.inv_id = tc.inv_id JOIN ops.turn t ON t.turn_id = ai.turn_id "
            "WHERE t.session_id = :s"
        ),
        {"s": sid},
    ).scalars().all()


def _install_chunk(conn, chunk_id, doc_id, locator, content, family_ids, division):
    embed_chunks(
        conn, [Chunk(chunk_id, doc_id, locator, [locator], content, 10, family_ids, division)],
        "r2026.08.1", embed=_VEC, settings=_SETTINGS,
    )


def test_product_advisor_searches_before_conceding_eiga(seeded_conn, make_ctx, new_session):
    _install_chunk(
        seeded_conn, "ch.eiga.0", "doc.process", "§Oxygen Compressors",
        "Oxygen compressors: Compliance with EIGA standard for cleaning procedure. Non-lubricated.",
        ["fam.process_recip"], "industrial",
    )
    fake = FakeLLM(
        {
            "triage": {
                "division": "industrial", "intent": "product_question", "language": "en",
                "in_scope": True, "pii_present": False, "confidence": 0.9,
            },
            "product_query": {
                "model_or_family": None, "is_price_or_leadtime": False,
                "search_query": "EIGA oxygen compliance",
            },
            # product_advisor searches the documents proactively, so the compose answers from the
            # EIGA hit (tr2) instead of conceding from the product name list alone.
            "answer": {
                "message": "Yes — our oxygen compressors meet the EIGA standard for cleaning [tr2].",
                "citations": ["tr2"],
            },
        }
    )
    ctx = make_ctx(fake, embed=_VEC, settings=_SETTINGS)
    sid = new_session()
    result = run_turn(ctx, sid, "Are your compressors EIGA compliant for oxygen service?")

    assert result.outcome == "answered"
    assert "search_documents" in _tool_names(seeded_conn, sid)  # searched, didn't concede blind
    assert "EIGA" in result.messages[-1]["text"]


def test_faq_searches_before_conceding_founder(seeded_conn, make_ctx, new_session):
    _install_chunk(
        seeded_conn, "ch.about.f", "doc.about", "§WHO WE ARE",
        "Jyotech Engineering was founded by Mr. Deepak Bhatia in 1991.", [], "industrial",
    )
    fake = FakeLLM(
        {
            "triage": {
                "division": "unknown", "intent": "faq", "language": "en",
                "in_scope": True, "pii_present": False, "confidence": 0.9,
            },
            "answer": [
                {"message": "I don't have the founder's name in our records.", "citations": []},
                {"message": "Jyotech was founded by Mr. Deepak Bhatia [tr2].", "citations": ["tr2"]},
            ],
        }
    )
    ctx = make_ctx(fake, embed=_VEC, settings=_SETTINGS)
    sid = new_session()
    result = run_turn(ctx, sid, "Who founded Jyotech?")

    assert result.outcome == "answered"
    assert "search_documents" in _tool_names(seeded_conn, sid)
    assert "Deepak Bhatia" in result.messages[-1]["text"]
