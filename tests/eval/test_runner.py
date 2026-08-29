"""Golden-suite runner (LLD-EVAL-02/03): a failing check fails the run; e2e stays pending."""

from __future__ import annotations

from agentkit.eval.runner import run_eval

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
    # the e2e layer is reported pending for every question, never failed
    for q in report.results:
        assert q.status("e2e") == "pending"


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
