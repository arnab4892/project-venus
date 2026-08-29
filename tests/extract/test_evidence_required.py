"""PRD-F-015 / LLD-EXT-04: the verbatim evidence gate. Pure-Python, no DB.

Accepts an exact substring, rejects a paraphrase, logs every drop, and never
"fixes" a field. Whitespace differences are tolerated (normalised); wording
differences are not.
"""

from __future__ import annotations

from agentkit.extract.evidence import apply_gate, is_verbatim, normalise_ws, unwrap

_SECTION = (
    "PROCESS COMPRESSORS (RECIP.)\n"
    "Reciprocating, Non-Lubricated, Water Cooled.\n"
    "Capacity range: Up to 20000 Nm3/hr. Discharge up to 1000 Barg.\n"
    "Suitable for hydrogen, natural gas, BOG."
)


def test_is_verbatim_accepts_exact_and_tolerates_whitespace() -> None:
    assert is_verbatim("Up to 20000 Nm3/hr", _SECTION)
    # Collapsed / re-wrapped whitespace still matches (normalisation only).
    assert is_verbatim("Non-Lubricated,   Water\nCooled", _SECTION)


def test_is_verbatim_rejects_paraphrase() -> None:
    assert not is_verbatim("maximum flow of 20000 normal cubic metres per hour", _SECTION)
    assert not is_verbatim("", _SECTION)


def test_gate_keeps_grounded_fields() -> None:
    obj = {
        "comp_type": {"value": "reciprocating", "evidence": "Reciprocating, Non-Lubricated"},
        "capacity": {"value": "up to 20000 Nm3/hr", "evidence": "Up to 20000 Nm3/hr"},
    }
    cleaned, drops = apply_gate(obj, _SECTION, section_id="doc.process::s003")
    assert drops == []
    assert unwrap(cleaned["capacity"]) == "up to 20000 Nm3/hr"


def test_gate_drops_and_logs_ungrounded_field() -> None:
    obj = {
        "capacity": {"value": "up to 20000 Nm3/hr", "evidence": "Up to 20000 Nm3/hr"},
        "discharge_p": {"value": "up to 1500 barg", "evidence": "Discharge up to 1500 Barg"},
    }
    cleaned, drops = apply_gate(obj, _SECTION, section_id="doc.process::s003")
    # The real text says 1000, not 1500 → the discharge field is dropped, not fixed.
    assert cleaned["discharge_p"] is None
    assert cleaned["capacity"] is not None
    assert len(drops) == 1
    assert drops[0].path == "discharge_p"
    assert drops[0].section_id == "doc.process::s003"
    assert "verbatim" in drops[0].reason
    # The dropped value is preserved in the log for review, never written as fact.
    assert drops[0].value == "up to 1500 barg"


def test_gate_prunes_failing_list_elements_only() -> None:
    obj = {
        "gases": [
            {"value": "hydrogen", "evidence": "hydrogen"},
            {"value": "oxygen", "evidence": "oxygen"},  # not in the section
            {"value": "BOG", "evidence": "BOG"},
        ]
    }
    cleaned, drops = apply_gate(obj, _SECTION, section_id="s1")
    kept = [unwrap(g) for g in cleaned["gases"]]
    assert kept == ["hydrogen", "BOG"]
    assert len(drops) == 1
    assert drops[0].value == "oxygen"


def test_is_verbatim_is_case_sensitive() -> None:
    # The section says lowercase "hydrogen"; a differently-cased quote is NOT verbatim.
    assert is_verbatim("hydrogen", _SECTION)
    assert not is_verbatim("HYDROGEN", _SECTION)


def test_gate_drops_case_mismatched_evidence() -> None:
    # A gas whose quote differs from the source only in case is dropped, not "fixed".
    obj = {"gases": [{"value": "hydrogen", "evidence": "HYDROGEN"}]}
    cleaned, drops = apply_gate(obj, _SECTION, section_id="s1")
    assert cleaned["gases"] == []
    assert len(drops) == 1


def test_normalise_ws_collapses_runs() -> None:
    assert normalise_ws("a  \n  b\t c") == "a b c"


def test_heading_cited_evidence_needs_heading_in_the_checked_text() -> None:
    # A field grounded in the section's HEADING (verbatim source) is dropped when the
    # gate sees only the body, but kept when the heading is included — the fix that
    # feeds Section.evidence_text (heading + body) into the gate.
    body = "Type: Reciprocating, Non-Lubricated, water cooled."
    heading = "OXYGEN COMPRESSORS"
    obj = {"family": {"value": "fam.oxygen_recip", "evidence": "OXYGEN COMPRESSORS"}}

    _, drops_body_only = apply_gate(obj, body, section_id="s1")
    assert len(drops_body_only) == 1                      # heading not in body → dropped

    cleaned, drops = apply_gate(obj, f"{heading}\n{body}", section_id="s1")
    assert drops == []                                    # heading present → kept
    assert unwrap(cleaned["family"]) == "fam.oxygen_recip"
