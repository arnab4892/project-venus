"""Grounding gate removes ungrounded claims (LLD-RT-05 / PRD-F-009), decision 3.

Two failure modes must both fall back to the honest "not published" reply and never ship the
draft: (a) an answer with no citation resolving to a real tool result, and (b) an answer whose
spec number (a figure next to a unit) is not in any tool result / arg / the user's own message —
even when a genuine citation sits beside it. A valid, fully-sourced answer passes.

Unit tests exercise ``ground_answer`` directly; a full-turn test proves the orchestrator ships
the fallback (not the fabricated text) and writes no citation rows for it.
"""

from __future__ import annotations

from tests.runtime._helpers import FakeLLM

from agentkit.runtime.grounding import (
    FALLBACK_TEXT,
    all_numbers,
    fallback_text,
    ground_answer,
    redact_unsourced_spec_numbers,
    spec_numbers,
)
from agentkit.runtime.ops import ToolCallRecord
from agentkit.runtime.orchestrator import run_turn
from sqlalchemy import text


def test_markdown_bold_and_table_do_not_hide_spec_numbers():
    """Part B formatting keeps each figure next to its unit, so the numeric guard still sees it.

    ``_SPEC_RE`` tolerates only whitespace between number and unit — the persona rule mandates
    number+unit stay in one bold span / one table cell (``**25,000 Nm³/hr**``, ``| 25,000 Nm³/hr |``),
    never split. These assert the guard is unaffected by the surrounding ``*`` / ``|``.
    """
    assert spec_numbers("**25,000 Nm3/hr**") == {25000.0}
    assert spec_numbers("| 25,000 Nm3/hr | recip |") == {25000.0}
    # all_numbers is a superset (it also picks the '3' inside the "Nm3" unit token, pre-existing
    # and harmless — the universe only needs to *contain* the figure); assert containment.
    assert 25000.0 in all_numbers("**25,000** Nm3/hr")
    assert {3000.0, 350.0} <= all_numbers("| 3,000 | 350 |")

    # an unsourced spec figure is still redacted when wrapped in bold or sitting in a table cell
    clean, stripped = redact_unsourced_spec_numbers("published up to **99,999 Nm3/hr**", set())
    assert stripped == [99999.0]
    assert "99,999" not in clean


def test_devanagari_spec_numbers_are_folded():
    """Devanagari digits (०-९) fold 1:1 to ASCII before the numeric guard, so a Devanagari figure
    can never slip past spec_numbers / all_numbers or survive redaction unnoticed (LLD-RT-05/07).

    The ASCII-digit persona rule is the intended behaviour; this is the safety net. Being 1:1
    single code points, folding preserves offsets — redaction stays aligned and normalises to ASCII.
    """
    assert spec_numbers("२५,००० Nm3/hr") == {25000.0}
    assert 25000.0 in all_numbers("२५,००० Nm3/hr")

    # an unsourced Devanagari spec figure is stripped exactly like an ASCII one
    clean, stripped = redact_unsourced_spec_numbers("published up to ९९,९९९ Nm3/hr", set())
    assert stripped == [99999.0]
    assert "९९,९९९" not in clean

    # a *sourced* Devanagari figure survives — and comes back normalised to ASCII digits
    clean, stripped = redact_unsourced_spec_numbers("up to २५,००० Nm3/hr", {25000.0})
    assert stripped == []
    assert "25,000 Nm3/hr" in clean


def test_fallback_ships_in_visitor_language():
    """A gate-stripped draft ships the fallback in the visitor's language (LLD-RT-07). The strings
    are fixed and pre-vetted — never LLM-translated (the gate fired because the model just failed).
    """
    for lang in ("hi", "hinglish"):
        gr = ground_answer("Our range covers this.", [], [_match_record()], ["duty"], language=lang)
        assert gr.ok is False
        assert gr.text == fallback_text(lang)
        assert gr.text != FALLBACK_TEXT  # not the English default
    # unknown / missing language falls back to English (back-compat)
    gr = ground_answer("Our range covers this.", [], [_match_record()], ["duty"], language=None)
    assert gr.text == FALLBACK_TEXT


def _match_record() -> ToolCallRecord:
    return ToolCallRecord(
        tr_id="tr1",
        tool="match_capability",
        args={"gas": "hydrogen", "capacity": 3000, "capacity_unit": "Nm3/hr", "discharge_p": 350},
        result={"matches": [{"cap_id": "cap.002", "family_id": "fam.process_recip"}], "near_edge": False},
        rows_returned=1,
        latency_ms=1,
    )


def test_uncited_answer_falls_back():
    gr = ground_answer("Our range covers this duty.", [], [_match_record()], ["hydrogen duty"])
    assert gr.ok is False
    assert gr.text == FALLBACK_TEXT
    assert gr.citations == []
    assert gr.grounding["status"] == "none"


def test_invented_spec_number_beside_real_citation_falls_back():
    # cites tr1 (real) but claims 99999 Nm3/hr — a figure in no source.
    gr = ground_answer(
        "That duty sits in our range, published up to 99999 Nm3/hr [tr1].",
        ["tr1"],
        [_match_record()],
        ["We need 3000 Nm3/hr at 350 bar"],
    )
    assert gr.ok is False
    assert gr.text == FALLBACK_TEXT
    assert "unsourced_numbers" in gr.grounding["reason"]


def test_fully_sourced_answer_passes():
    gr = ground_answer(
        "That duty (3000 Nm3/hr at 350 bar) sits in our process range [tr1].",
        ["tr1"],
        [_match_record()],
        ["We need 3000 Nm3/hr from 20 to 350 bar"],
    )
    assert gr.ok is True
    assert gr.grounding["status"] == "full"
    assert any(c.kind == "capability" and c.ref_id == "cap.002" for c in gr.citations)


def _fabricating_fake(language: str = "en") -> FakeLLM:
    return FakeLLM(
        {
            "triage": {
                "division": "industrial", "intent": "application_enquiry", "language": language,
                "in_scope": True, "pii_present": False, "confidence": 0.95,
            },
            "application_slots": {
                "gas": "hydrogen", "capacity": 3000, "capacity_unit": "Nm3/hr", "discharge_p": 350,
                "lubricated": False, "standard": None, "industry": None, "timeline": None,
                "asked_slot": None, "message": "",
            },
            # invents "99999 Nm3/hr" — must be stripped to the fallback
            "answer": {
                "message": "We can go up to 99999 Nm3/hr for this [tr1].",
                "citations": ["tr1"],
            },
        }
    )


def test_full_turn_ships_fallback_not_fabrication(seeded_conn, make_ctx, new_session):
    ctx = make_ctx(_fabricating_fake())
    sid = new_session()
    result = run_turn(ctx, sid, "Hydrogen, 3000 Nm3/hr at 350 bar, oil-free")

    assert result.grounding["status"] == "none"
    assert result.messages[-1]["text"] == FALLBACK_TEXT
    # a gate-stripped turn is recorded as `fallback`, not `answered` (countable in ops)
    assert result.outcome == "fallback"
    # the fabricated figure never reaches an ops.message
    persisted = seeded_conn.execute(
        text("SELECT text FROM ops.message WHERE session_id = :s AND role = 'assistant'"),
        {"s": sid},
    ).scalars().all()
    assert all("99999" not in (t or "") for t in persisted)
    # nothing fabricated was cited
    assert seeded_conn.execute(
        text("SELECT count(*) FROM ops.citation WHERE turn_id = (SELECT turn_id FROM ops.turn WHERE session_id = :s)"),
        {"s": sid},
    ).scalar_one() == 0


def test_full_turn_ships_hindi_fallback(seeded_conn, make_ctx, new_session):
    """A gate strip on a Hindi turn ships the Hindi fallback, not the English one (LLD-RT-07)."""
    ctx = make_ctx(_fabricating_fake("hi"))
    sid = new_session()
    result = run_turn(ctx, sid, "हाइड्रोजन, 3000 Nm3/hr, 350 bar, oil-free")

    assert result.outcome == "fallback"
    assert result.messages[-1]["text"] == fallback_text("hi")
    assert result.messages[-1]["text"] != FALLBACK_TEXT
