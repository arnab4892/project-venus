"""n_ground persists the resolved sources onto the answer payload — and fails open (LLD-RT-05).

The resolver is presentational: a lookup failure must never break a turn. These tests drive the
``ground`` node directly with a hand-built answer + tool result.
"""

from __future__ import annotations

from agentkit.runtime import orchestrator
from agentkit.runtime.agents.base import AgentOutput
from agentkit.runtime.orchestrator import Ctx, WorkflowState, n_ground
from agentkit.runtime.ops import ToolCallRecord

_FACT_RESULT = {
    "facts": [
        {"fact_id": "cf.001", "value": "ISO 9001:2015", "source_locator": "§Certifications",
         "source_doc_id": "doc.about"}
    ]
}


def _answer_wf(conn) -> WorkflowState:
    ctx = Ctx(conn=conn, client="jyotech", complete=lambda *a, **k: "")
    wf = WorkflowState(ctx=ctx, session_id=None, latest_user="Which ISO certs do you hold?", history=[])
    wf.agent_out = AgentOutput(
        action="answer",
        draft_text="We hold ISO 9001:2015 certification.",
        citations=["t1"],
    )
    wf.tool_records = [
        ToolCallRecord(tr_id="t1", tool="get_company_fact", args={"kind": "certification"},
                       result=_FACT_RESULT, rows_returned=1, latency_ms=0)
    ]
    return wf


def test_n_ground_attaches_resolved_sources(seeded_conn) -> None:
    wf = _answer_wf(seeded_conn)
    n_ground({"wf": wf})

    assert wf.outcome == "answered"
    payload = wf.reply_messages[0].payload
    assert payload is not None and "sources" in payload
    src = payload["sources"]
    assert src and src[0]["title"] == "About Us"  # doc.about, resolved from the fact's source doc
    assert src[0]["kind"] == "web"
    assert src[0]["location"] == "Certifications"


def test_n_ground_fails_open_when_resolver_raises(monkeypatch) -> None:
    def _boom(*_a, **_k):
        raise RuntimeError("resolver blew up")

    monkeypatch.setattr(orchestrator, "resolve_sources", _boom)

    wf = _answer_wf(conn=None)  # conn unused once the resolver is stubbed to raise
    n_ground({"wf": wf})

    # The turn still completes with its answer; no sources are attached.
    assert wf.outcome == "answered"
    assert wf.reply_messages[0].text == "We hold ISO 9001:2015 certification."
    assert wf.reply_messages[0].payload is None
