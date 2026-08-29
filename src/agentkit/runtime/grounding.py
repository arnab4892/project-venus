"""Grounding gate (LLD-RT-05, PRD-F-008/F-009).

Every factual answer must be backed by this turn's tool results / retrieved chunks. The 5a
gate (decision 3, confirmed) has two conditions:

* **(a) citation-backed** — an ``answer`` must carry ≥1 citation that resolves to a real
  tool-result from this turn;
* **(b) numeric guard** — every *spec number* in the answer (a number adjacent to a
  capacity/pressure/measure unit) must appear in this turn's tool results, tool args or the
  visitor's own message. An unsourced spec number is treated exactly like an uncited answer.

If either fails, the drafted answer is dropped and replaced with a "not in our published
material" + handoff-offer reply — an invented spec never ships, even beside a genuine
citation. Per-claim NLI splitting (the fuller LLD-RT-05 wording) is a deferred refinement.

Citation rows for the reply/``ops.citation`` are derived here from the cited tool results
(locator + url where the tool provides them, PRD-F-008).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from agentkit.runtime.ops import CitationRecord, ToolCallRecord

FALLBACK_TEXT = (
    "I don't have that in our published material. I can connect you with our engineers "
    "who can help with the specifics — would you like me to do that?"
)

# Units that mark a number as a *spec* (capacity / pressure / measure). Longer tokens first.
_UNIT = (
    r"(?:n\s*m\s*3\s*/?\s*hr?|nm³/\s*hr|scmd|scmh|kg\s*/\s*hr|kg\s*/\s*cm2g|kg\s*/\s*cm²g|"
    r"m3\s*/\s*hr|m³/\s*hr|lpm|cfm|tpd|barg|bar|psi|kw|hp|lumen|tons?)"
)
_SPEC_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*" + _UNIT, re.IGNORECASE)
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
# Citation markers like "[1]" carry an incidental digit that is not a spec — drop them first.
_CITATION_MARKER_RE = re.compile(r"\[\d+\]")


def _to_float(token: str) -> float:
    return float(token.replace(",", "").replace(" ", ""))


def _all_numbers(text: str) -> set[float]:
    return {_to_float(m.group(0)) for m in _NUMBER_RE.finditer(text or "")}


def _spec_numbers(text: str) -> set[float]:
    cleaned = _CITATION_MARKER_RE.sub(" ", text or "")
    return {_to_float(m.group(1)) for m in _SPEC_RE.finditer(cleaned)}


def _citations_from_record(rec: ToolCallRecord) -> list[CitationRecord]:
    """Map a cited tool result to its citation rows (locator/url where published)."""
    out: list[CitationRecord] = []
    r = rec.result
    if rec.tool == "match_capability":
        for m in r.get("matches", []):
            out.append(CitationRecord(kind="capability", ref_id=m["cap_id"]))
    elif rec.tool == "search_documents":
        chunks = r.get("chunks", [])
        if chunks:
            c = chunks[0]  # cite the top hit
            out.append(
                CitationRecord(kind="chunk", ref_id=c["chunk_id"], locator=c.get("locator"), url=c.get("url"))
            )
    elif rec.tool == "get_company_fact":
        for f in r.get("facts", []):
            out.append(
                CitationRecord(kind="fact", ref_id=f["fact_id"], locator=f.get("source_locator"))
            )
    elif rec.tool in ("get_product", "list_products"):
        for p in r.get("products", []):
            out.append(
                CitationRecord(kind="product", ref_id=p.get("product_id") or p.get("family_id"),
                               locator=p.get("source_locator"))
            )
    elif rec.tool == "get_office":
        office = r.get("office")
        if office:
            out.append(CitationRecord(kind="office", ref_id=office["office_id"]))
    return out


@dataclass
class GroundingResult:
    ok: bool
    text: str
    citations: list[CitationRecord] = field(default_factory=list)
    grounding: dict = field(default_factory=dict)


def ground_answer(
    answer_text: str,
    cited_tr_ids: list[str],
    tool_records: list[ToolCallRecord],
    user_texts: list[str],
) -> GroundingResult:
    """Apply the citation + numeric gate to a drafted answer.

    Returns a :class:`GroundingResult`: on pass, the original text + derived citation rows and
    ``status="full"``; on fail, the fallback text, no citations and ``status="none"``.
    """
    by_id = {rec.tr_id: rec for rec in tool_records}
    cited = [tid for tid in cited_tr_ids if tid in by_id]

    # allowed numeric universe: everything the tools returned / were asked, plus the visitor's
    # own numbers (a number the user supplied is not a hallucination).
    allowed: set[float] = set()
    for rec in tool_records:
        allowed |= _all_numbers(json.dumps(rec.result, default=str))
        allowed |= _all_numbers(json.dumps(rec.args, default=str))
    for t in user_texts:
        allowed |= _all_numbers(t)

    spec_numbers = _spec_numbers(answer_text)
    offending = {n for n in spec_numbers if n not in allowed}

    claims = max(len(spec_numbers), 1)
    ok = bool(cited) and not offending

    if ok:
        citations: list[CitationRecord] = []
        for tid in cited:
            citations.extend(_citations_from_record(by_id[tid]))
        return GroundingResult(
            ok=True,
            text=answer_text,
            citations=citations,
            grounding={"claims": claims, "grounded": claims, "status": "full"},
        )

    return GroundingResult(
        ok=False,
        text=FALLBACK_TEXT,
        citations=[],
        grounding={
            "claims": claims,
            "grounded": 0,
            "status": "none",
            "reason": "uncited" if not cited else f"unsourced_numbers:{sorted(offending)}",
        },
    )
