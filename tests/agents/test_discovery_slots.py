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
                "message": "Our Process Gas Compressor range fits that duty "
                           "(3000 Nm3/hr at 350 bar) [tr1].",
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


def test_figure_carrying_slot_question_is_replaced_by_template(seeded_conn, make_ctx, new_session):
    # Fix 2 (ask_slot hygiene): a slot question that carries any spec-number is an answer in
    # disguise the numeric guard would redact to a mutilated "up to … and …". Instead of shipping
    # the redaction, the LLM message is DISCARDED and the deterministic _SLOT_QUESTIONS template is
    # sent — so the customer never sees figures or ellipses in a clarifying question.
    from agentkit.runtime.agents.application_discovery import _SLOT_QUESTIONS

    ctx = make_ctx(_parroted_slot_fake())
    sid = new_session()
    result = run_turn(ctx, sid, "I need something for natural gas")  # no flow/pressure given

    assert result.outcome == "asked_slot"
    assert _tool_names(seeded_conn, sid) == []  # no match_capability this turn
    text = result.messages[0]["text"]
    assert text == _SLOT_QUESTIONS["capacity"]  # the clean template, not the figure-carrying message
    assert "25,000" not in text and "25000" not in text and "…" not in text


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
            "answer": {"message": "Our Natural Gas Compressor range fits that duty [tr1].",
                       "citations": ["tr1"]},
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
            "answer": {"message": "Our Natural Gas Compressor range fits that duty [tr1].",
                       "citations": ["tr1"]},
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


# ---- follow-up duty carry-forward (LLD-AG-01, followup-carryforward) ----------

def _TRI():
    return {"division": "industrial", "intent": "application_enquiry", "language": "en",
            "in_scope": True, "pii_present": False, "confidence": 0.95}


def _prior_hydrogen_slots():
    return {"gas": "hydrogen", "capacity": 24000, "capacity_unit": "SCMD", "discharge_p": 350,
            "lubricated": False, "standard": None, "industry": None, "timeline": None,
            "asked_slot": None, "message": ""}


def _followup_fake(turn2_slots) -> FakeLLM:
    ans = {"message": "Our Process Gas Compressor range fits that duty [tr1].", "citations": ["tr1"]}
    return FakeLLM({
        "triage": [_TRI(), _TRI()],
        "application_slots": [_prior_hydrogen_slots(), turn2_slots],
        "answer": [ans, ans, ans],  # turn 1 + up to a compose+recompose for turn 2
    })


_TURN1 = "We need to compress hydrogen at 24000 SCMD, from 20 to 350 bar. Oil-free is mandatory."


def _hist(user_text: str) -> list[dict]:
    # What rebuild_context passes on a follow-up turn: the prior turn's user + assistant messages.
    # The token guard scans the USER turns, so an oil-free stated in turn 1 survives into turn 2.
    return [{"role": "user", "text": user_text}, {"role": "assistant", "text": "Understood."}]


def test_followup_carry_forward_merges_prior_duty(seeded_conn, make_ctx, new_session):
    # "What about 80000 SCMD?" carries gas + discharge_p + oil-free forward from turn 1's duty.
    ctx = make_ctx(_followup_fake(
        {"gas": None, "capacity": 80000, "capacity_unit": "SCMD", "discharge_p": None,
         "lubricated": None, "standard": None, "industry": None, "timeline": None,
         "asked_slot": None, "message": ""}))
    sid = new_session()
    run_turn(ctx, sid, _TURN1)
    run_turn(ctx, sid, "What about 80000 SCMD?", history=_hist(_TURN1))
    calls = _match_args_list(seeded_conn, sid)
    assert any(c["capacity"] == 80000 and c["gas"] == "hydrogen" and c["discharge_p"] == 350
               and c["lubricated"] is False for c in calls)


def test_followup_gas_override_inherits_flow_and_pressure(seeded_conn, make_ctx, new_session):
    # "and what about oxygen?" — new gas wins, flow + pressure inherit.
    ctx = make_ctx(_followup_fake(
        {"gas": "oxygen", "capacity": None, "capacity_unit": None, "discharge_p": None,
         "lubricated": None, "standard": None, "industry": None, "timeline": None,
         "asked_slot": None, "message": ""}))
    sid = new_session()
    run_turn(ctx, sid, _TURN1)
    run_turn(ctx, sid, "and what about oxygen?", history=_hist(_TURN1))
    calls = _match_args_list(seeded_conn, sid)
    assert any(c["gas"] == "oxygen" and c["capacity"] == 24000 and c["capacity_unit"] == "SCMD"
               and c["discharge_p"] == 350 for c in calls)


def test_full_restatement_inherits_nothing(seeded_conn, make_ctx, new_session):
    # A complete new duty overrides everything — the prior oil-free must NOT carry onto it.
    ctx = make_ctx(_followup_fake(
        {"gas": "nitrogen", "capacity": 5000, "capacity_unit": "Nm3/hr", "discharge_p": 400,
         "lubricated": None, "standard": None, "industry": None, "timeline": None,
         "asked_slot": None, "message": ""}))
    sid = new_session()
    run_turn(ctx, sid, _TURN1)
    run_turn(ctx, sid, "Actually, nitrogen at 5000 Nm3/hr and 400 bar.", history=_hist(_TURN1))
    calls = _match_args_list(seeded_conn, sid)
    new = [c for c in calls if c["gas"] == "nitrogen"]
    assert new and all(c["capacity"] == 5000 and c["capacity_unit"] == "Nm3/hr"
                       and c["discharge_p"] == 400 and c["lubricated"] is None for c in new)


def test_unitless_followup_asks_one_question_no_match(seeded_conn, make_ctx, new_session):
    # A new flow with no unit is ambiguous — ask, never match on a guessed (inherited) unit.
    from agentkit.runtime.agents.application_discovery import _SLOT_QUESTIONS

    # The slot LLM INFERS capacity_unit=SCMD from context — but the message has no unit, so the
    # message-based ambiguity check must still ask (never assume a unit for a bare number).
    ctx = make_ctx(_followup_fake(
        {"gas": None, "capacity": 80000, "capacity_unit": "SCMD", "discharge_p": None,
         "lubricated": None, "standard": None, "industry": None, "timeline": None,
         "asked_slot": None, "message": ""}))
    sid = new_session()
    run_turn(ctx, sid, _TURN1)
    r2 = run_turn(ctx, sid, "what about 80000?", history=_hist(_TURN1))
    assert r2.outcome == "asked_slot"
    assert len(_match_args_list(seeded_conn, sid)) == 1  # only turn 1 matched; turn 2 asked
    assert r2.messages[0]["text"] == _SLOT_QUESTIONS["capacity"]


def test_guards_run_after_merge_strip_invented_lubricated(seeded_conn, make_ctx, new_session):
    # No user turn mentions lubrication anywhere; a turn-2 invented lubricated=false is stripped
    # AFTER the merge (guard runs on the merged slots).
    fake = FakeLLM({
        "triage": [_TRI(), _TRI()],
        "application_slots": [
            {"gas": "natural gas", "capacity": 20000, "capacity_unit": "SCMD", "discharge_p": 100,
             "lubricated": None, "standard": None, "industry": None, "timeline": None,
             "asked_slot": None, "message": ""},
            {"gas": None, "capacity": 40000, "capacity_unit": "SCMD", "discharge_p": None,
             "lubricated": False, "standard": None, "industry": None, "timeline": None,
             "asked_slot": None, "message": ""},
        ],
        "answer": [{"message": "Our Natural Gas Compressor range fits [tr1].", "citations": ["tr1"]}] * 3,
    })
    ctx = make_ctx(fake)
    sid = new_session()
    run_turn(ctx, sid, "Natural gas, 20000 SCMD, 100 bar")          # no lubrication word
    run_turn(ctx, sid, "What about 40000 SCMD?",
             history=_hist("Natural gas, 20000 SCMD, 100 bar"))  # invents lubricated=false
    calls = _match_args_list(seeded_conn, sid)
    assert all(c["lubricated"] is None for c in calls)  # never a token → always stripped


def test_prior_duty_fail_open_on_read_error():
    # The carry-forward read is a bolt-on — any exception must be swallowed (log + None), never
    # raised, so the turn proceeds without inheritance.
    from agentkit.runtime.agents.application_discovery import _prior_duty

    class _CM:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _RaisingConn:
        def begin_nested(self):
            return _CM()

        def execute(self, *a, **k):
            raise RuntimeError("db exploded")

    assert _prior_duty(_RaisingConn(), "sess") is None


def test_carry_forward_read_failure_does_not_break_the_turn(seeded_conn, make_ctx, new_session):
    # A real conn where ONLY the prior-duty read (its distinctive SQL) raises — the follow-up turn
    # must still complete (the savepoint rolls the failed read back; the shared transaction survives).
    import dataclasses

    ctx = make_ctx(_followup_fake(
        {"gas": None, "capacity": 80000, "capacity_unit": "SCMD", "discharge_p": None,
         "lubricated": None, "standard": None, "industry": None, "timeline": None,
         "asked_slot": None, "message": ""}))
    sid = new_session()
    run_turn(ctx, sid, _TURN1)  # persists turn 1's match_capability

    class _Proxy:
        def __init__(self, real):
            self._real = real

        def execute(self, statement, *a, **k):
            if "ORDER BY t.seq DESC" in str(statement):  # the _prior_duty read
                raise RuntimeError("simulated prior-duty read failure")
            return self._real.execute(statement, *a, **k)

        def __getattr__(self, name):
            return getattr(self._real, name)

    ctx2 = dataclasses.replace(ctx, conn=_Proxy(seeded_conn))
    r2 = run_turn(ctx2, sid, "What about 80000 SCMD?", history=_hist(_TURN1))
    assert r2.outcome in ("answered", "asked_slot", "handoff")  # completed, did not crash


def test_answer_missing_family_name_recomposes_then_fallback(seeded_conn, make_ctx, new_session):
    # A "no results" denial composed despite a real match (turn 74d45612) → recompose once, then
    # the honest fallback naming the family (never ship a tool-denial).
    denial = {"message": "Hmm, I'm not getting any results from our search right now.",
              "citations": ["tr1"]}
    fake = FakeLLM({
        "triage": _TRI(),
        "application_slots": {"gas": "hydrogen", "capacity": 3000, "capacity_unit": "Nm3/hr",
                              "discharge_p": 350, "lubricated": False, "standard": None,
                              "industry": None, "timeline": None, "asked_slot": None, "message": ""},
        "answer": [denial, denial],
    })
    ctx = make_ctx(fake)
    sid = new_session()
    result = run_turn(ctx, sid, "hydrogen, 3000 Nm3/hr, 350 bar, oil-free")
    assert result.outcome == "handoff"
    assert fake.seen.count("answer") == 2  # exactly one recompose
    assert "Process Gas Compressor" in result.messages[0]["text"]


# ---- sources prompt-hygiene safety net (followup-carryforward) ----------------

def test_strip_empty_sources_trailer_unit():
    from agentkit.runtime.agents.base import strip_empty_sources_trailer as s

    assert s("What flow do you need?\n\nSources: []") == "What flow do you need?"
    assert s("What flow do you need?\nSources:") == "What flow do you need?"
    assert s("Q?\n\nSource: none") == "Q?"
    assert s("Q?\nSources: (none)") == "Q?"
    assert s("Q?\nSources: —") == "Q?"
    assert s("Q?\nSources: N/A") == "Q?"
    # conservative: a line naming real sources is preserved
    assert s("Answer.\nSources: Process catalogue p5") == "Answer.\nSources: Process catalogue p5"
    # no-op when there is no sources line
    assert s("Just a plain question?") == "Just a plain question?"
    assert s("") == ""


def test_ask_slot_strips_empty_sources_trailer(seeded_conn, make_ctx, new_session):
    # The observed leak: the slot model echoes the persona's provenance text as a "Sources: []"
    # trailer. It must be stripped so the customer sees only the question — no citations on a
    # clarifying turn (still asked_slot, still no match_capability).
    fake = FakeLLM({
        "triage": {"division": "industrial", "intent": "application_enquiry", "language": "en",
                   "in_scope": True, "pii_present": False, "confidence": 0.95},
        "application_slots": {
            "gas": "hydrogen", "capacity": None, "capacity_unit": None, "discharge_p": None,
            "lubricated": None, "standard": None, "industry": None, "timeline": None,
            "asked_slot": "capacity",
            "message": "Yes — what flow rate do you need, in Nm³/hr (or kg/hr / SCMD)?\n\nSources: []",
        },
    })
    ctx = make_ctx(fake)
    sid = new_session()
    result = run_turn(ctx, sid, "We need to compress hydrogen at our plant — can you help?")
    assert result.outcome == "asked_slot"
    assert _tool_names(seeded_conn, sid) == []
    text = result.messages[0]["text"]
    assert "Sources" not in text and "Source:" not in text
    assert "what flow rate do you need" in text.lower()
