"""Application Discovery slot-filling (LLD-AG-01 / PRD-F-002).

The agent asks exactly ONE missing slot per turn and never invents a value: given a message with
only the gas, it must ask for capacity and must NOT call ``match_capability`` (no complete duty
to match). When all three required slots are present it calls the tool. Runs full turns on the
seeded DB with the ``complete=`` seam.
"""

from __future__ import annotations

from tests.runtime._helpers import FakeLLM

from agentkit.runtime.orchestrator import run_turn
from sqlalchemy import text


def _partial_fake() -> FakeLLM:
    return FakeLLM(
        {
            "triage": {
                "division": "industrial", "intent": "application_enquiry", "language": "en",
                "in_scope": True, "pii_present": False, "confidence": 0.95,
            },
            # only the gas is known; capacity + discharge_p missing
            "application_slots": {
                "gas": "hydrogen", "capacity": None, "capacity_unit": None, "discharge_p": None,
                "lubricated": None, "standard": None, "industry": None, "timeline": None,
                "asked_slot": "capacity", "message": "What flow/capacity do you need (with units)?",
            },
        }
    )


def _tool_names(conn, sid) -> list[str]:
    return conn.execute(
        text(
            "SELECT tc.tool FROM ops.tool_call tc JOIN ops.agent_invocation ai "
            "ON ai.inv_id = tc.inv_id JOIN ops.turn t ON t.turn_id = ai.turn_id "
            "WHERE t.session_id = :s"
        ),
        {"s": sid},
    ).scalars().all()


def test_asks_one_slot_and_calls_no_tool_when_incomplete(seeded_conn, make_ctx, new_session):
    ctx = make_ctx(_partial_fake())
    sid = new_session()
    result = run_turn(ctx, sid, "I need something for hydrogen")

    assert result.outcome == "asked_slot"
    assert len(result.messages) == 1  # exactly one question bubble
    # never guessed a duty → match_capability was NOT called
    assert _tool_names(seeded_conn, sid) == []
    # the persisted invocation recorded the asked slot, and did not invent capacity/pressure
    slots = seeded_conn.execute(
        text(
            "SELECT slots FROM ops.agent_invocation WHERE agent = 'application_discovery' "
            "AND turn_id = (SELECT turn_id FROM ops.turn WHERE session_id = :s)"
        ),
        {"s": sid},
    ).scalar_one()
    import json

    slots = slots if isinstance(slots, dict) else json.loads(slots)
    assert slots.get("gas") == "hydrogen"
    assert "capacity" not in slots and "discharge_p" not in slots


def _complete_fake() -> FakeLLM:
    return FakeLLM(
        {
            "triage": {
                "division": "industrial", "intent": "application_enquiry", "language": "en",
                "in_scope": True, "pii_present": False, "confidence": 0.95,
            },
            "application_slots": {
                "gas": "hydrogen", "capacity": 3000, "capacity_unit": "Nm3/hr", "discharge_p": 350,
                "lubricated": False, "standard": None, "industry": None, "timeline": None,
                "asked_slot": None, "message": "",
            },
            "answer": {
                "message": "That duty (3000 Nm3/hr at 350 bar) sits in our process range [tr1].",
                "citations": ["tr1"],
            },
        }
    )


def test_calls_match_capability_when_slots_complete(seeded_conn, make_ctx, new_session):
    ctx = make_ctx(_complete_fake())
    sid = new_session()
    result = run_turn(ctx, sid, "hydrogen, 3000 Nm3/hr, 350 bar, oil-free")
    assert result.outcome == "answered"
    assert "match_capability" in _tool_names(seeded_conn, sid)


def _parroted_slot_fake() -> FakeLLM:
    # asked_slot whose QUESTION parrots published figures (25,000 / 1,000) from the exemplar,
    # with no tool call this turn — the gate-bypass shape (turn 271ff0e2).
    return FakeLLM(
        {
            "triage": {
                "division": "industrial", "intent": "application_enquiry", "language": "en",
                "in_scope": True, "pii_present": False, "confidence": 0.9,
            },
            "application_slots": {
                "gas": "natural gas", "capacity": None, "capacity_unit": None, "discharge_p": None,
                "lubricated": None, "standard": None, "industry": None, "timeline": None,
                "asked_slot": "capacity",
                "message": "Sure — our process range goes up to 25,000 Nm³/hr and 1,000 barg. "
                           "What flow do you need?",
            },
        }
    )


def test_gate_strips_unsourced_figures_from_a_slot_question(seeded_conn, make_ctx, new_session):
    # A slot question is an answer in disguise if it carries published figures that never came
    # from a tool call or the user — the numeric guard strips them regardless of the asked_slot
    # outcome (LLD-RT-05, every message).
    ctx = make_ctx(_parroted_slot_fake())
    sid = new_session()
    result = run_turn(ctx, sid, "I need something for natural gas")  # no flow/pressure given

    assert result.outcome == "asked_slot"
    assert _tool_names(seeded_conn, sid) == []  # no match_capability this turn
    text = result.messages[0]["text"]
    assert "25,000" not in text and "25000" not in text
    assert "1,000 barg" not in text
    assert result.grounding and 25000.0 in result.grounding["stripped_unsourced_numbers"]


def _underextracted_then_complete_fake() -> FakeLLM:
    # The slot LLM first UNDER-extracts a complete duty (asks for capacity), then the enforced
    # re-extraction returns the full duty → match_capability must run.
    return FakeLLM(
        {
            "triage": {
                "division": "industrial", "intent": "application_enquiry", "language": "en",
                "in_scope": True, "pii_present": False, "confidence": 0.95,
            },
            "application_slots": [
                {"gas": "natural gas", "capacity": None, "capacity_unit": None, "discharge_p": None,
                 "lubricated": None, "standard": None, "industry": None, "timeline": None,
                 "asked_slot": "capacity", "message": "What flow do you need?"},
                {"gas": "natural gas", "capacity": 50000, "capacity_unit": "SCMD", "discharge_p": 120,
                 "lubricated": None, "standard": None, "industry": None, "timeline": None,
                 "asked_slot": None, "message": ""},
            ],
            "answer": {"message": "That natural gas duty fits our range [tr1].", "citations": ["tr1"]},
        }
    )


def test_complete_duty_forces_match_even_when_slot_llm_underextracts(seeded_conn, make_ctx, new_session):
    ctx = make_ctx(_underextracted_then_complete_fake())
    sid = new_session()
    result = run_turn(ctx, sid, "natural gas, 50000 SCMD, 120 bar")

    assert result.outcome == "answered"  # not asked_slot — the duty was complete
    assert "match_capability" in _tool_names(seeded_conn, sid)


# ---- filter-slot token guard (LLD-AG-01, slot-guard-conversion) ---------------

def _duty_fake(lubricated=False, standard=None) -> FakeLLM:
    return FakeLLM(
        {
            "triage": {
                "division": "industrial", "intent": "application_enquiry", "language": "en",
                "in_scope": True, "pii_present": False, "confidence": 0.95,
            },
            "application_slots": {
                "gas": "natural gas", "capacity": 50000, "capacity_unit": "SCMD", "discharge_p": 120,
                "lubricated": lubricated, "standard": standard, "industry": None, "timeline": None,
                "asked_slot": None, "message": "",
            },
            "answer": {"message": "That natural gas duty fits our range [tr1].", "citations": ["tr1"]},
        }
    )


def _match_args_list(conn, sid) -> list[dict]:
    import json

    rows = conn.execute(
        text(
            "SELECT tc.args FROM ops.tool_call tc JOIN ops.agent_invocation ai "
            "ON ai.inv_id = tc.inv_id JOIN ops.turn t ON t.turn_id = ai.turn_id "
            "WHERE t.session_id = :s AND tc.tool = 'match_capability'"
        ),
        {"s": sid},
    ).scalars().all()
    return [r if isinstance(r, dict) else json.loads(r) for r in rows]


def test_gas_engine_driven_strips_inferred_lubricated(seeded_conn, make_ctx, new_session):
    # The regression: "gas engine driven" is a prime mover, not a lubrication statement. The slot
    # LLM emitted lubricated=False; the guard must null it before match_capability is called.
    ctx = make_ctx(_duty_fake(lubricated=False))
    sid = new_session()
    run_turn(ctx, sid, "Natural gas, 50000 SCMD, wellhead application, gas engine driven, 120 bar")
    calls = _match_args_list(seeded_conn, sid)
    assert calls and all(c["lubricated"] is None for c in calls)


def test_explicit_oil_free_preserves_lubricated(seeded_conn, make_ctx, new_session):
    # An explicit lubrication statement must survive the guard (the filter is legitimate).
    ctx = make_ctx(_duty_fake(lubricated=False))
    sid = new_session()
    run_turn(ctx, sid, "Natural gas, 50000 SCMD, 120 bar, oil-free please")
    calls = _match_args_list(seeded_conn, sid)
    assert any(c["lubricated"] is False for c in calls)


def test_inferred_standard_stripped_without_code(seeded_conn, make_ctx, new_session):
    ctx = make_ctx(_duty_fake(lubricated=None, standard="API-618"))
    sid = new_session()
    run_turn(ctx, sid, "Natural gas, 50000 SCMD, 120 bar, gas engine driven")
    calls = _match_args_list(seeded_conn, sid)
    assert calls and all(c["standard"] is None for c in calls)


def test_named_standard_preserved(seeded_conn, make_ctx, new_session):
    ctx = make_ctx(_duty_fake(lubricated=None, standard="API-618"))
    sid = new_session()
    run_turn(ctx, sid, "Natural gas, 50000 SCMD, 120 bar, must be API-618 compliant")
    calls = _match_args_list(seeded_conn, sid)
    assert any(c["standard"] == "API-618" for c in calls)


# ---- direct token-matrix + guard unit tests ----------------------------------

def test_message_states_lubrication_matrix():
    from agentkit.runtime.agents.application_discovery import _message_states_lubrication

    for t in ["oil-free please", "oil free", "oilless design", "non-lube", "we build it lubricated",
              "non-lubricated", "ऑयल-फ्री कंप्रेसर चाहिए", "तेल-मुक्त मशीन", "लुब्रिकेटेड"]:
        assert _message_states_lubrication(t), t
    for t in ["gas engine driven", "oilfield gas", "engine driven", "just some oil in the sump",
              "motor driven", "wellhead application"]:
        assert not _message_states_lubrication(t), t


def test_message_states_standard_matrix():
    from agentkit.runtime.agents.application_discovery import _message_states_standard

    for t in ["API-618", "api 618", "ISO 9001", "NFPA 1936", "EN 469", "IS 4863", "ASME section",
              "ATEX zone 1"]:
        assert _message_states_standard(t), t
    # "capacity is 3000 Nm3/hr" must NOT match (lowercase "is"+digit; case-sensitive short guard),
    # and plain prose must not validate a hallucinated standard.
    for t in ["capacity is 3000 Nm3/hr", "this is a booster", "gas engine driven", "no code here"]:
        assert not _message_states_standard(t), t


def test_guard_strips_absent_preserves_present_and_multiturn():
    from agentkit.runtime.agents.application_discovery import _guard_inferred_filters

    # strip both when neither token appears
    s = {"lubricated": False, "standard": "API-618"}
    stripped = _guard_inferred_filters(s, "gas engine driven, 50000 SCMD, 120 bar", [])
    assert s["lubricated"] is None and s["standard"] is None
    assert stripped == {"lubricated": False, "standard": "API-618"}

    # preserve when the token is in the current message
    s = {"lubricated": False}
    _guard_inferred_filters(s, "oil-free, 50000 SCMD, 120 bar", [])
    assert s["lubricated"] is False

    # preserve when stated in a PRIOR USER turn (multi-turn)
    s = {"lubricated": False}
    _guard_inferred_filters(s, "natural gas, 50000 SCMD, 120 bar",
                            [{"role": "user", "text": "we need it oil-free"}])
    assert s["lubricated"] is False

    # accepted limitation: a token only in an ASSISTANT turn does NOT preserve it
    s = {"lubricated": False}
    _guard_inferred_filters(s, "yes, that one",
                            [{"role": "assistant", "text": "we build these oil-free"}])
    assert s["lubricated"] is None

    # Devanagari lubrication statement preserves
    s = {"lubricated": False}
    _guard_inferred_filters(s, "ऑयल-फ्री कंप्रेसर चाहिए, 50000 SCMD, 120 bar", [])
    assert s["lubricated"] is False
