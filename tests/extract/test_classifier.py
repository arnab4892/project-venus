"""LLD-EXT-02: classifier routing on fixture sections. LLM mocked, no DB."""

from __future__ import annotations

import json

from agentkit.extract.classify import classify_section, classify_sections
from agentkit.extract.markdown import Section


def _section(sid: str, heading: str, text: str, kind: str = "html") -> Section:
    return Section(
        section_id=sid, doc_id="doc.x", kind=kind, url="http://x",
        heading_path=[heading], page_start=None, page_end=None, text=text,
    )


def _fake_complete(messages, schema, name):
    """Route by keyword in the section text — a stand-in for the real classifier."""
    text = messages[-1]["content"].lower()
    if "nm3/hr" in text or "compressor range" in text:
        t = "capability_spec"
    elif "mch-" in text or "model" in text:
        t = "product_list"
    elif "iso 9001" in text or "certified" in text:
        t = "company_fact"
    elif "office" in text or "phone" in text:
        t = "office_contact"
    else:
        t = "other"
    return json.dumps({"type": t, "confidence": 0.92})


def test_routes_each_section_type() -> None:
    cases = {
        "s1": _section("s1", "Process", "Capacity up to 20000 Nm3/hr, water cooled."),
        "s2": _section("s2", "Breathing Air", "Model MCH-16 electric driven."),
        "s3": _section("s3", "About", "ISO 9001:2015 certified since 1991."),
        "s4": _section("s4", "Contact", "Mumbai Office phone +91-22-000."),
        "s5": _section("s5", "Careers", "Join our team, apply now."),
    }
    got = {
        sid: classify_section(sec, "PROMPT", complete=_fake_complete).type
        for sid, sec in cases.items()
    }
    assert got == {
        "s1": "capability_spec",
        "s2": "product_list",
        "s3": "company_fact",
        "s4": "office_contact",
        "s5": "other",
    }


def test_other_is_identifiable_for_skipping() -> None:
    secs = [
        _section("s1", "Process", "Capacity up to 20000 Nm3/hr."),
        _section("s5", "Careers", "Join our team."),
    ]
    results = classify_sections(secs, "PROMPT", complete=_fake_complete)
    kept = [c for c in results if c.type != "other"]
    skipped = [c for c in results if c.type == "other"]
    assert [c.section_id for c in kept] == ["s1"]
    assert [c.section_id for c in skipped] == ["s5"]


def test_invalid_response_falls_back_to_other() -> None:
    def bad(messages, schema, name):
        return "this is not json"

    sec = _section("s9", "Weird", "garbled")
    assert classify_section(sec, "PROMPT", complete=bad).type == "other"
