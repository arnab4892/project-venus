"""The optional ``Ctx.on_stage`` seam fires once per node, in graph order (Part A / dev-tooling).

A full offline turn with ``on_stage`` set records one human label per orchestrator node —
triage → route → agent → ground → respond — in execution order. The CLI leaves ``on_stage``
None, so this behaviour is inert there; the live Gradio harness plugs a queue in to drive its
honest per-node progress indicator. Every route traverses all five nodes, so the label sequence
is stable regardless of intent.
"""

from __future__ import annotations

from tests.runtime._helpers import FakeLLM

from agentkit.runtime.orchestrator import run_turn

EXPECTED_LABELS = [
    "Understanding your question",
    "Finding the right specialist",
    "Looking into it",
    "Checking our sources",
    "Finishing up",
]


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
            "answer": {
                "message": (
                    "That duty (3000 Nm3/hr at 350 bar) sits within our reciprocating, "
                    "non-lubricated process gas compressor range [tr1]."
                ),
                "citations": ["tr1"],
            },
        }
    )


def test_on_stage_records_labels_in_node_order(seeded_conn, make_ctx, new_session):
    labels: list[str] = []
    ctx = make_ctx(_fake())
    ctx.on_stage = labels.append  # the read-only progress seam

    run_turn(ctx, new_session(), "hydrogen, 3000 Nm3/hr, 20→350 bar, oil-free")

    assert labels == EXPECTED_LABELS


def test_on_stage_default_none_is_inert(seeded_conn, make_ctx, new_session):
    # The CLI path leaves on_stage unset; the turn must run without ever calling it.
    ctx = make_ctx(_fake())
    assert ctx.on_stage is None
    result = run_turn(ctx, new_session(), "hydrogen, 3000 Nm3/hr, 20→350 bar, oil-free")
    assert result.outcome == "answered"
