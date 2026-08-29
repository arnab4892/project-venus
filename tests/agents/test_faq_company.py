"""FAQ / Company agent answers from company facts with a citation (LLD-AG-06 / PRD-F-008).

"Are you ISO certified?" → the agent fetches ``get_company_fact('certification')`` and composes a
grounded answer citing the fact rows (with their locator). Runs a full turn on the seeded DB;
``search_documents`` fallback is skipped on the chunk-less demo seed (best-effort).
"""

from __future__ import annotations

from tests.runtime._helpers import FakeLLM

from agentkit.runtime.orchestrator import run_turn
from sqlalchemy import text


def _fake() -> FakeLLM:
    return FakeLLM(
        {
            "triage": {
                "division": "unknown", "intent": "faq", "language": "en",
                "in_scope": True, "pii_present": False, "confidence": 0.95,
            },
            "answer": {
                "message": "Jyotech holds ISO 9001:2015, ISO 14001:2015 and ISO 45001:2018 [tr1].",
                "citations": ["tr1"],
            },
        }
    )


def test_iso_answer_with_citation(seeded_conn, make_ctx, new_session):
    ctx = make_ctx(_fake())
    sid = new_session()
    result = run_turn(ctx, sid, "Are you ISO certified?")

    assert result.route == "faq_company"
    assert result.outcome == "answered"
    assert result.grounding["status"] == "full"
    assert "ISO 9001" in result.messages[-1]["text"]

    # citations resolve to certification fact rows (with locator)
    fact_cites = [c for c in result.citations if c["kind"] == "fact"]
    assert fact_cites and all(c["ref_id"].startswith("cf.") for c in fact_cites)
    assert any(c["locator"] for c in fact_cites)

    # get_company_fact was logged to ops.tool_call
    tools = seeded_conn.execute(
        text(
            "SELECT tc.tool FROM ops.tool_call tc JOIN ops.agent_invocation ai "
            "ON ai.inv_id = tc.inv_id JOIN ops.turn t ON t.turn_id = ai.turn_id "
            "WHERE t.session_id = :s"
        ),
        {"s": sid},
    ).scalars().all()
    assert "get_company_fact" in tools
