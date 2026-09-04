"""Application Discovery answers from the matched ROW, retrieves for colour (LLD-AG-01 fix).

After the match_capability redesign, the agent must: (1) pick the top match, (2) call
search_documents filtered to THAT family only with a family-name+gas query, and (3) feed the
compose step the row's OWN published limits (never a chunk's number). DB-free: a fake ToolRunner
returns a canned match result and records the search call; the ``complete=`` seam captures the
compose prompt so we can assert the row figures reached it.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from agentkit.runtime.agents import application_discovery
from agentkit.runtime.ops import ToolCallRecord

_TOP_MATCH = {
    "cap_id": "cap.doc_jyotech_catalog_process_s005.0",
    "family_id": "fam.process_recip",
    "family_name": "Process Compressors (Recip.)",
    "capacity_min": None, "capacity_max": 25000, "capacity_unit": "Nm3/hr",
    "discharge_p_min": None, "discharge_p_max": 1000, "pressure_unit": "barg",
    "standards": ["API-618 or equivalent"],
    "lubricated": None, "lubricated_unspecified": True,
    "headroom": {"capacity": 0.88, "pressure": 0.65},
    "near_edge": False,
}
_MATCH_RESULT = {
    "matches": [_TOP_MATCH],
    "non_comparable_candidates": [
        {"cap_id": "cap.fuel", "family_id": "fam.hydrogen_fuelling_system",
         "family_name": "Hydrogen Fuelling Systems", "reason": "capacity not published"},
    ],
    "any_near_edge": False,
}


class FakeTools:
    """Mimics ToolRunner's surface for the agent, without a DB."""

    def __init__(self, match_result):
        self.records: list[ToolCallRecord] = []
        self._match_result = match_result
        self.search_calls: list[tuple] = []

    def match_capability(self, **args):
        rec = ToolCallRecord("tr1", "match_capability", args, self._match_result, 1, 1)
        self.records.append(rec)
        return rec

    def search_documents(self, query, **kw):
        self.search_calls.append((query, kw))
        result = {"chunks": [{
            "chunk_id": "ch.jyotech_catalog_process.0003", "doc_id": "doc.jyotech_catalog_process",
            "locator": "p4-5 §PROCESS COMPRESSORS (RECIP.)",
            "url": "https://www.jyotech.com/pdf/PROCESS.pdf",
            "content_md": "Process reciprocating compressors for hydrogen and process gases.",
            "family_ids": ["fam.process_recip"],
        }]}
        rec = ToolCallRecord(f"tr{len(self.records) + 1}", "search_documents", {"query": query, **kw}, result, 1, 1)
        self.records.append(rec)
        return rec


class CapturingLLM:
    """complete= seam: slot JSON for the slot call, answer JSON for compose; captures messages."""

    def __init__(self):
        self.answer_system = None

    def __call__(self, messages, schema, name):
        if name == "application_slots":
            return json.dumps({
                "gas": "hydrogen", "capacity": 3000, "capacity_unit": "Nm3/hr", "discharge_p": 350,
                "lubricated": False, "standard": None, "industry": None, "timeline": None,
                "asked_slot": None, "message": "",
            })
        if name == "answer":
            self.answer_system = messages[0]["content"]
            return json.dumps({
                "message": "That hydrogen duty sits within our Process Compressors (Recip.) "
                           "range, published up to 25000 Nm3/hr and 1000 barg [tr1].",
                "citations": ["tr1"],
            })
        raise AssertionError(f"unexpected schema {name}")


def test_answers_from_top_match_and_retrieves_that_family_only():
    llm = CapturingLLM()
    ctx = SimpleNamespace(complete=llm)
    tools = FakeTools(_MATCH_RESULT)

    out = application_discovery.run(
        ctx, prompt_body="You are the application agent.",
        triage={"division": "industrial", "language": "en"},
        history=[], latest_user="hydrogen, 3000 Nm3/hr, 350 bar, oil-free", tools=tools,
    )

    assert out.action == "answer"
    assert out.output["matched_family_id"] == "fam.process_recip"
    assert out.output["near_edge"] is False

    # retrieval was filtered to the matched family ONLY, with a family-name+gas query
    assert len(tools.search_calls) == 1
    query, kw = tools.search_calls[0]
    assert query == "Process Compressors (Recip.) hydrogen"
    assert kw["family_ids"] == ["fam.process_recip"]

    # the compose step was handed the ROW's own published limits, formatted for prose (25,000 /
    # 1000 — Indian grouping ≥10,000), not a chunk's figures
    assert "25,000" in llm.answer_system
    assert "1000" in llm.answer_system
    assert "Process Compressors (Recip.)" in llm.answer_system


def test_no_matches_offers_engineer_review_not_a_wrong_family():
    llm = CapturingLLM()
    ctx = SimpleNamespace(complete=llm)
    # only non-comparable candidates, no matches
    tools = FakeTools({"matches": [], "non_comparable_candidates": _MATCH_RESULT["non_comparable_candidates"],
                       "any_near_edge": False})

    out = application_discovery.run(
        ctx, prompt_body="x", triage={"division": "industrial", "language": "en"},
        history=[], latest_user="hydrogen, 3000 Nm3/hr, 350 bar", tools=tools,
    )
    assert out.action == "handoff"
    assert out.output["reason"] == "no_match"
    # no compose call happened (no fabricated family answer)
    assert llm.answer_system is None
    # no retrieval attempted when there is nothing to support
    assert tools.search_calls == []


def test_no_match_handoff_ships_in_visitor_language():
    """The no-match handoff stub is a fixed per-language template (LLD-RT-07) — a hi/hinglish turn
    gets a hi/hinglish message, not English. The visitor's own gas/capacity/pressure stay English.
    """
    no_match = {"matches": [], "non_comparable_candidates": _MATCH_RESULT["non_comparable_candidates"],
                "any_near_edge": False}
    for lang in ("hi", "hinglish"):
        tools = FakeTools(no_match)
        out = application_discovery.run(
            SimpleNamespace(complete=CapturingLLM()), prompt_body="x",
            triage={"division": "industrial", "language": lang},
            history=[], latest_user="hydrogen, 3000 Nm3/hr, 350 bar", tools=tools,
        )
        assert out.action == "handoff"
        text = out.messages[0].text
        assert text == application_discovery._NOMATCH_TEMPLATES[lang].format(
            gas="hydrogen", capacity=3000, capacity_unit="Nm3/hr", discharge_p=350,
            note=application_discovery._NOMATCH_NOTE[lang],
        )
        # the visitor's own values are still present, in English
        assert "hydrogen" in text and "Nm3/hr" in text
        # and it is NOT the English default template
        assert text != application_discovery._NOMATCH_TEMPLATES["en"].format(
            gas="hydrogen", capacity=3000, capacity_unit="Nm3/hr", discharge_p=350,
            note=application_discovery._NOMATCH_NOTE["en"],
        )


def test_hi_nomatch_template_prose_is_register_pure():
    """The hi no-match handoff prose must be pure written Hindi (LLD-RT-07), asserted with the same
    checker the eval gate uses. The `{...}` slots (the visitor's own gas/capacity/pressure values,
    which stay English) are stripped by the checker, so this tests the hand-authored prose only."""
    from agentkit.eval.runner import script_purity_offenders

    assert script_purity_offenders(application_discovery._NOMATCH_TEMPLATES["hi"]) == []
    assert script_purity_offenders(application_discovery._NOMATCH_NOTE["hi"]) == []
