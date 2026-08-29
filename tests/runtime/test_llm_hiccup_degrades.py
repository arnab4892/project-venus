"""A runtime LLM returning invalid JSON degrades gracefully — the turn never crashes (LLD-RT).

A self-hosted model can occasionally return junk. When `call_json` exhausts its retries and
raises `ExtractionSkipped`, the orchestrator must catch it and fall back to a clarify reply, not
propagate the exception. Covers both the triage call and an agent call failing.
"""

from __future__ import annotations

from tests.runtime._helpers import activate_all_prompts

from agentkit.client_config import gas_alias_map
from agentkit.runtime.orchestrator import LLM_HICCUP_MESSAGE, Ctx, run_turn
from sqlalchemy import text


def _ctx(seeded_conn, complete):
    activate_all_prompts(seeded_conn)
    return Ctx(conn=seeded_conn, client="jyotech", complete=complete,
               gas_aliases=gas_alias_map("jyotech"))


def _bad_complete(messages, schema, name):
    return "not json at all"  # every call → invalid JSON → ExtractionSkipped after retries


def test_triage_llm_invalid_degrades_to_clarify(seeded_conn, new_session):
    ctx = _ctx(seeded_conn, _bad_complete)
    sid = new_session()
    result = run_turn(ctx, sid, "hello there")
    # triage failed → low-confidence default → clarify; no crash
    assert result.outcome == "clarify"
    # a turn + messages were still persisted (append-only), triage invocation recorded the error
    out = seeded_conn.execute(
        text("SELECT output FROM ops.agent_invocation WHERE agent='triage' "
             "AND turn_id=(SELECT turn_id FROM ops.turn WHERE session_id=:s)"),
        {"s": sid},
    ).scalar_one()
    import json

    out = out if isinstance(out, dict) else json.loads(out)
    assert out.get("error") == "triage_llm_invalid"


def _triage_ok_then_bad(responses):
    """complete seam: valid triage JSON first, invalid for everything after."""
    import json

    def _c(messages, schema, name):
        if name == "triage":
            return json.dumps(responses["triage"])
        return "still not json"

    return _c


def test_agent_llm_invalid_degrades_to_clarify(seeded_conn, new_session):
    triage = {"division": "industrial", "intent": "application_enquiry", "language": "en",
              "in_scope": True, "pii_present": False, "confidence": 0.95}
    ctx = _ctx(seeded_conn, _triage_ok_then_bad({"triage": triage}))
    sid = new_session()
    result = run_turn(ctx, sid, "hydrogen, 3000 Nm3/hr, 350 bar")
    # triage routed to application_discovery, but the slot-extraction call returned junk →
    # graceful clarify, not a crash.
    assert result.route == "application_discovery"
    assert result.outcome == "clarify"
    assert result.messages[-1]["text"] == LLM_HICCUP_MESSAGE
