"""The §4 hydrogen turn produces the data-model-shaped ops rows (LLD-RT, PRD-F-008).

Runs the data-model §4 opening message through a full turn on the seeded DB (demo release), with
the ``complete=`` seam supplying triage → complete slots → a grounded answer. Per the confirmed
division of labour (decision 2), this asserts the §4 ops-row SHAPE and that each citation
resolves to a real tool result of the turn — the demo ids ``cap.002`` / ``fam.process_recip``.
The exact live id ``cap.doc_jyotech_catalog_process_s005.0`` is pinned by the golden suite
(``cap-hydrogen-process``) and the live ``chat --show-trace`` acceptance, not here.

The demo seed has no chunks, so ``search_documents`` is skipped (best-effort) and the answer is
carried by the capability match alone — enough to prove the shape and the grounding gate.
"""

from __future__ import annotations

from tests.runtime._helpers import FakeLLM

from agentkit.runtime.orchestrator import run_turn
from sqlalchemy import text

HYDROGEN = (
    "We need to compress hydrogen, about 3000 Nm3/hr from 20 to 350 bar. Oil-free is mandatory."
)


def _fake() -> FakeLLM:
    return FakeLLM(
        {
            "triage": {
                "division": "industrial",
                "intent": "application_enquiry",
                "language": "en",
                "in_scope": True,
                "pii_present": False,
                "confidence": 0.95,
            },
            "application_slots": {
                "gas": "hydrogen",
                "capacity": 3000,
                "capacity_unit": "Nm3/hr",
                "discharge_p": 350,
                "lubricated": False,
                "standard": None,
                "industry": "refinery",
                "timeline": None,
                "asked_slot": None,
                "message": "",
            },
            # Grounded draft: every spec number (3000 Nm3/hr, 350 bar) is sourced from the
            # match args / the user's own message; cites the match_capability result tr1.
            "answer": {
                "message": (
                    "That duty (3000 Nm3/hr at 350 bar) sits within our reciprocating, "
                    "non-lubricated process gas compressor range [tr1]. Exact frame and staging "
                    "come from our engineers."
                ),
                "citations": ["tr1"],
            },
        }
    )


def test_hydrogen_turn_shape_and_citation(seeded_conn, make_ctx, new_session):
    ctx = make_ctx(_fake())  # no embed → search_documents skipped on the chunk-less demo seed
    sid = new_session()
    result = run_turn(ctx, sid, HYDROGEN)

    # outcome + grounding
    assert result.route == "application_discovery"
    assert result.outcome == "answered"
    assert result.grounding["status"] == "full"

    # a capability citation resolving to the demo-seed cap the tool actually returned
    cap_cites = [c for c in result.citations if c["kind"] == "capability"]
    assert cap_cites, "expected a capability citation"
    assert cap_cites[0]["ref_id"] == "cap.002"

    # ---- §4-shaped ops rows ----
    turn_ids = seeded_conn.execute(
        text("SELECT turn_id FROM ops.turn WHERE session_id = :s"), {"s": sid}
    ).scalars().all()
    assert len(turn_ids) == 1
    tid = turn_ids[0]

    # session ×1 (created), 2+ messages (user + assistant)
    assert seeded_conn.execute(
        text("SELECT count(*) FROM ops.message WHERE turn_id = :t"), {"t": tid}
    ).scalar_one() >= 2
    roles = seeded_conn.execute(
        text("SELECT role FROM ops.message WHERE turn_id = :t ORDER BY seq_in_turn"), {"t": tid}
    ).scalars().all()
    assert roles[0] == "user" and "assistant" in roles

    # agent_invocation: triage + application_discovery
    agents = seeded_conn.execute(
        text("SELECT agent FROM ops.agent_invocation WHERE turn_id = :t"), {"t": tid}
    ).scalars().all()
    assert "triage" in agents and "application_discovery" in agents

    # tool_call: match_capability logged (ops.tool_call logging begins this milestone)
    tools = seeded_conn.execute(
        text(
            "SELECT tool FROM ops.tool_call tc JOIN ops.agent_invocation ai "
            "ON ai.inv_id = tc.inv_id WHERE ai.turn_id = :t"
        ),
        {"t": tid},
    ).scalars().all()
    assert "match_capability" in tools

    # citation row persisted for the capability
    cited = seeded_conn.execute(
        text("SELECT kind, ref_id FROM ops.citation WHERE turn_id = :t"), {"t": tid}
    ).mappings().all()
    assert any(c["kind"] == "capability" and c["ref_id"] == "cap.002" for c in cited)
