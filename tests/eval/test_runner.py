"""Golden-suite runner (LLD-EVAL-02/03): fact/retrieval/e2e scoring + activation gating.

e2e runs the real orchestrator through the ``complete=`` seam; without a runtime LLM it is
**skipped** (never failed), so the hermetic fact/retrieval gate still works.
"""

from __future__ import annotations

from tests.runtime._helpers import FakeLLM

from agentkit.eval.runner import (
    normalize_digits,
    run_eval,
    script_purity_offenders,
    text_contains,
)


# --- matcher: number typography must not defeat contains / must_not_contain -------

def test_contains_matches_across_digit_grouping_and_decimals():
    assert text_contains("published up to 25,000 Nm3/hr", "25000")
    assert text_contains("published up to 25000.0 Nm3/hr", "25000")
    assert text_contains("up to 1,00,000 SCMD", "100000")


def test_must_not_contain_still_catches_grouped_forbidden_number():
    # a 20000 guard must catch the rejected figure even when it is grouped/decimalised
    assert text_contains("Capacity Range: Up to 20,000 Nm3/hr", "20000")
    assert text_contains("20000.0 Nm3/hr", "20000")
    # but a genuinely different number is not a false hit
    assert not text_contains("25,000 Nm3/hr", "20000")


def test_normalize_digits():
    assert normalize_digits("25,000") == "25000"
    assert normalize_digits("25000.0") == "25000"
    assert normalize_digits("1,00,000.00") == "100000"
    # Devanagari numerals fold to ASCII (safety net) so a Devanagari figure still matches an
    # ASCII needle and can't slip past a must_not guard unnoticed (LLD-RT-07).
    assert normalize_digits("२५,०००") == "25000"
    assert text_contains("published up to २५,००० Nm3/hr", "25000")
    assert text_contains("Capacity: २०,००० Nm3/hr", "20000")


def test_markdown_bold_and_table_pipes_do_not_break_matching():
    # Part B renders bold names and small tables; the eval matcher (case-insensitive +
    # digit-normalised substring) must be blind to the surrounding * / | characters.
    assert text_contains("**25,000** Nm3/hr", "25000")
    assert text_contains("| 25,000 Nm3/hr | recip |", "25000")
    assert text_contains("The **MCH-16** is medium-duty", "mch-16")
    # a must_not_contain guard still catches a forbidden needle inside bold / a table cell
    assert text_contains("**Source:** Jyotech Catalog", "source:")
    assert text_contains("| catalogue | link |", "catalogue")


# --- script purity (LLD-RT-07): strict Devanagari register check --------------------------------

def test_script_purity_pure_hindi_reply_passes():
    # Pure written Hindi with a bold Latin product/family name, a unit and a standard code — the
    # three allowed exceptions. Nothing to report.
    reply = (
        "यह ज़रूरत हमारी **Process Compressors (Recip.)** श्रेणी में आराम से आती है — "
        "25,000 Nm³/hr और 1,000 barg तक, API-618 के अनुसार। क्या मैं आपको हमारे इंजीनियरों से जोड़ूँ?"
    )
    assert script_purity_offenders(reply) == []


def test_script_purity_one_stray_english_word_fails_and_is_named():
    reply = "यह ज़रूरत हमारी **Process Compressors** श्रेणी में published है।"
    assert script_purity_offenders(reply) == ["published"]


def test_script_purity_lists_every_offender_in_order():
    reply = "यह available और suitable विकल्प आपकी requirement के लिए है।"
    assert script_purity_offenders(reply) == ["available", "suitable", "requirement"]


def test_script_purity_bold_names_units_codes_digits_markers_never_trigger():
    # Bold product/family/brand names, the full unit set, standard codes, ASCII + Devanagari
    # figures and inline citation markers are all inert — none is an "English word" leak.
    assert script_purity_offenders("**MP/HP Air & Gas Compressors** और **Jyotech** — ठीक है।") == []
    assert script_purity_offenders("९–१० HP, 265 lpm, 20000 SCMD, 5 kW, 850 barg, 40 kg/hr — सब ठीक।") == []
    assert script_purity_offenders("**Jyotech** के पास ISO 9001:2015, EN और NFPA हैं।") == []
    assert script_purity_offenders("यह ठीक है [tr1]। और यह भी [tr2, tr3]।") == []


def test_script_purity_folds_no_devanagari_or_punctuation_into_offenders():
    # A reply with only Devanagari words, digits and punctuation has no Latin runs at all.
    assert script_purity_offenders("२५,००० तक की क्षमता — बढ़िया! क्या मैं जोड़ूँ?") == []


_HYDROGEN_ARGS = {
    "gas": "hydrogen",
    "capacity": 3000,
    "capacity_unit": "Nm3/hr",
    "discharge_p": 350,
    "lubricated": False,
}


def test_failing_question_fails_the_run_and_e2e_is_pending(seeded_conn):
    questions = [
        {
            "id": "cap-pass",
            "question": "hydrogen 3000 Nm3/hr @ 350 barg oil-free",
            "expected_outcome": "answer",
            "fact": {
                "tool": "match_capability",
                "args": _HYDROGEN_ARGS,
                "expect_ids": ["fam.process_recip"],
            },
        },
        {
            "id": "cap-fail",
            "question": "expects a family that isn't returned",
            "expected_outcome": "answer",
            "fact": {
                "tool": "match_capability",
                "args": _HYDROGEN_ARGS,
                "expect_ids": ["fam.does_not_exist"],
            },
        },
    ]
    report = run_eval(seeded_conn, "jyotech", questions=questions)

    assert report.ok is False  # one failing question fails the whole run
    by_id = {q.id: q for q in report.results}
    assert by_id["cap-pass"].status("fact") == "pass"
    assert by_id["cap-fail"].status("fact") == "fail"
    # without a runtime LLM the e2e layer is skipped (never failed), for every question
    for q in report.results:
        assert q.status("e2e") == "skip"


def test_all_passing_run_is_ok(seeded_conn):
    questions = [
        {
            "id": "office-south",
            "question": "Who covers the South?",
            "expected_outcome": "answer",
            "fact": {
                "tool": "get_office",
                "args": {"region": "South"},
                "expect_ids": ["off.chn"],
            },
        }
    ]
    report = run_eval(seeded_conn, "jyotech", questions=questions)
    assert report.ok is True
    assert report.results[0].status("fact") == "pass"
    assert report.results[0].status("retrieval") == "na"  # no retrieval block


# --- e2e layer (real orchestrator through the complete= seam) ---------------

def _capability_fake(answer_message: str) -> FakeLLM:
    """Drives a full application-enquiry turn: triage → slots (complete) → grounded answer."""
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
            "answer": {"message": answer_message, "citations": ["tr1"]},
        }
    )


def _capability_fake_seq(answer_messages: list[str]) -> FakeLLM:
    """Like ``_capability_fake`` but the ``answer`` schema is a FIFO queue: attempt N gets
    ``answer_messages[N]``. Used to script fail-then-pass across the e2e retry."""
    fake = _capability_fake(answer_messages[0])
    fake._responses["answer"] = [{"message": m, "citations": ["tr1"]} for m in answer_messages]
    return fake


_E2E_Q = {
    "id": "e2e-cap",
    "question": "hydrogen 3000 Nm3/hr at 350 bar oil-free",
    "expected_outcome": "answer",
    "expected_answer_contains": ["process gas"],
    "expected_source_ids": ["fam.process_recip"],
}

_PASS_MSG = "That duty fits our process gas reciprocating range [tr1]."
_FAIL_MSG = "Our process gas reciprocating range; near-edge — talk to engineers [tr1]."


def test_e2e_layer_passes_a_scripted_conversation(seeded_conn):
    fake = _capability_fake("That duty fits our process gas reciprocating range [tr1].")
    report = run_eval(
        seeded_conn, "jyotech", questions=[_E2E_Q], complete=fake, layers=("e2e",)
    )
    assert report.ok is True
    assert report.results[0].status("e2e") == "pass"


def test_e2e_must_not_contain_fails_the_run(seeded_conn):
    fake = _capability_fake("Our process gas reciprocating range; near-edge — talk to engineers [tr1].")
    q = {**_E2E_Q, "must_not_contain": ["near-edge"]}
    report = run_eval(seeded_conn, "jyotech", questions=[q], complete=fake, layers=("e2e",))
    assert report.ok is False
    assert report.results[0].status("e2e") == "fail"


def test_e2e_missing_expected_text_fails(seeded_conn):
    fake = _capability_fake("We can help with that duty [tr1].")  # lacks "process gas"
    report = run_eval(
        seeded_conn, "jyotech", questions=[_E2E_Q], complete=fake, layers=("e2e",)
    )
    assert report.ok is False
    assert report.results[0].status("e2e") == "fail"


def test_e2e_script_purity_flags_latin_leak_in_hindi_reply(seeded_conn):
    # `script_purity: hi` opts a question into the strict register check: a Hindi reply that copies
    # English words from the tool vocabulary fails and the offenders are named (LLD-RT-07). The
    # reply is properly cited so the grounding gate keeps it (else the pure fallback would ship).
    leak = "यह ड्यूटी हमारी process gas श्रेणी में published range के भीतर आराम से आती है [tr1]।"
    fake = _capability_fake(leak)
    q = {**_E2E_Q, "expected_answer_contains": [], "expected_source_ids": [], "script_purity": "hi"}
    report = run_eval(seeded_conn, "jyotech", questions=[q], complete=fake, layers=("e2e",))
    assert report.ok is False
    assert report.results[0].status("e2e") == "fail"
    detail = next(r.detail for r in report.results[0].layers if r.layer == "e2e")
    assert "script purity" in detail
    assert "published" in detail and "process" in detail


def test_e2e_script_purity_passes_a_pure_hindi_reply(seeded_conn):
    pure = "यह ड्यूटी हमारी प्रोसेस गैस श्रेणी में आराम से आती है [tr1]।"
    fake = _capability_fake(pure)
    q = {**_E2E_Q, "expected_answer_contains": [], "expected_source_ids": [], "script_purity": "hi"}
    report = run_eval(seeded_conn, "jyotech", questions=[q], complete=fake, layers=("e2e",))
    assert report.ok is True
    assert report.results[0].status("e2e") == "pass"


def test_e2e_retry_pass_is_flaky(seeded_conn):
    # First attempt trips must_not_contain, the single retry passes → FLAKY: counts as a pass
    # for the gate but the report keeps the FIRST attempt's evidence, labelled (LLD-EVAL-02).
    fake = _capability_fake_seq([_FAIL_MSG, _PASS_MSG])
    q = {**_E2E_Q, "must_not_contain": ["near-edge"]}
    report = run_eval(seeded_conn, "jyotech", questions=[q], complete=fake, layers=("e2e",))
    assert report.ok is True  # flaky does not block the gate
    assert report.results[0].status("e2e") == "flaky"
    detail = next(r.detail for r in report.results[0].layers if r.layer == "e2e")
    assert "flaky (passed on retry)" in detail
    assert "near-edge" in detail  # the first attempt's failing evidence is preserved
    # counts surface the flaky bucket so variance stays visible
    assert report.counts()["e2e"].get("flaky") == 1


def test_e2e_two_consecutive_failures_block(seeded_conn):
    # Both attempts fail → a real failure that blocks the gate (no flaky rescue).
    fake = _capability_fake_seq([_FAIL_MSG, _FAIL_MSG])
    q = {**_E2E_Q, "must_not_contain": ["near-edge"]}
    report = run_eval(seeded_conn, "jyotech", questions=[q], complete=fake, layers=("e2e",))
    assert report.ok is False
    assert report.results[0].status("e2e") == "fail"
    assert "flaky" not in report.counts()["e2e"]


def test_fact_layer_failure_is_never_retried_or_flaky(seeded_conn):
    # Fact is deterministic: a failure stays a hard fail — the retry mechanism is e2e-only.
    q = {
        "id": "cap-fail",
        "question": "expects a family that isn't returned",
        "expected_outcome": "answer",
        "fact": {"tool": "match_capability", "args": _HYDROGEN_ARGS,
                 "expect_ids": ["fam.does_not_exist"]},
    }
    report = run_eval(seeded_conn, "jyotech", questions=[q], layers=("fact",))
    assert report.ok is False
    assert report.results[0].status("fact") == "fail"
    assert "flaky" not in report.counts().get("fact", {})


def test_activation_blocked_on_e2e_failure(seeded_conn):
    from agentkit.runtime.prompts import activate_prompt_gated, load_prompts
    from sqlalchemy import text

    written = load_prompts(seeded_conn, "jyotech")
    pid = next(w["prompt_id"] for w in written if w["agent"] == "triage")
    fake = _capability_fake("Our process gas range; near-edge [tr1].")
    q = {**_E2E_Q, "must_not_contain": ["near-edge"]}

    activated, report = activate_prompt_gated(
        seeded_conn, pid, "jyotech", questions=[q], complete=fake
    )
    assert activated is False
    assert not report.ok
    # activation rolled back — the prompt stays inactive
    assert not seeded_conn.execute(
        text("SELECT is_active FROM ops.prompt_version WHERE prompt_id = :p"), {"p": pid}
    ).scalar_one()
