"""Golden-suite runner (LLD-EVAL-02/03): fact/retrieval/e2e scoring + activation gating.

e2e runs the real orchestrator through the ``complete=`` seam; without a runtime LLM it is
**skipped** (never failed), so the hermetic fact/retrieval gate still works.
"""

from __future__ import annotations

from tests.runtime._helpers import FakeLLM

from agentkit.eval.runner import normalize_digits, run_eval, text_contains


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


def test_markdown_bold_and_table_pipes_do_not_break_matching():
    # Part B renders bold names and small tables; the eval matcher (case-insensitive +
    # digit-normalised substring) must be blind to the surrounding * / | characters.
    assert text_contains("**25,000** Nm3/hr", "25000")
    assert text_contains("| 25,000 Nm3/hr | recip |", "25000")
    assert text_contains("The **MCH-16** is medium-duty", "mch-16")
    # a must_not_contain guard still catches a forbidden needle inside bold / a table cell
    assert text_contains("**Source:** Jyotech Catalog", "source:")
    assert text_contains("| catalogue | link |", "catalogue")

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


_E2E_Q = {
    "id": "e2e-cap",
    "question": "hydrogen 3000 Nm3/hr at 350 bar oil-free",
    "expected_outcome": "answer",
    "expected_answer_contains": ["process gas"],
    "expected_source_ids": ["fam.process_recip"],
}


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
