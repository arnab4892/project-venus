"""FAQ / Company agent (LLD-AG-06, and the company-facts slice of LLD-AG-03).

Answers company questions (certifications, founding, facilities, coverage, clients, industries,
contacts), **office / branch / regional-coverage** questions, and general "about us" / document
questions. It fetches ``get_company_fact`` for the kind implied by the question, resolves the
covering office via ``get_office`` for a location question, and augments with a
``search_documents`` fallback, then composes a grounded answer citing the tool-result ids
(locator + url, PRD-F-008). Every fact is sourced; if nothing relevant is found the grounding
gate falls the reply back honestly.
"""

from __future__ import annotations

import re

from sqlalchemy import text as _text

from agentkit.runtime.agents.base import AgentOutput, compose_grounded_answer

# A machine/product design-or-compliance question (usually routed to documents_compliance, but a
# borderline one can land here): the company_fact table won't hold it, so faq_company must SEARCH
# the catalogues before it could ever concede absence (LLD-AG absence protocol).
_STANDARD_RE = re.compile(
    r"\bapi[\s\-]?\d|\basme\b|\bped\b|\batex\b|\biso\s?1363|\bis[\s\-]?\d|\ben[\s\-]?\d|\bbs[\s\-]?\d|"
    r"compliant|compliance|certified to|conform|rated to|"
    r"machine|compressor|product|equipment|model",
    re.IGNORECASE,
)


def _is_product_or_standard_question(text: str) -> bool:
    """True if the question is about a machine/product or a design/compliance standard."""
    return bool(_STANDARD_RE.search(text or ""))

# Question keyword → company_fact kind. A question may hit SEVERAL (LLD-AG-06 multi-part
# decomposition): "who founded X and do you export to Y" → founder + founded + coverage. Every
# matching kind is looked up and answered independently — one missing kind never sinks the rest.
_KIND_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("certif", "iso", "accredit"), "certification"),
    (("founder", "founded", "who found", "started by", "who is behind"), "founder"),
    (("founded", "establish", "since", "how old", "which year", "when was"), "founded"),
    (("facilit", "manufactur", "plant", "factory"), "facility"),
    (("coverage", "countr", "export", "middle east", "abroad", "overseas", "presence"), "coverage"),
    (("client", "customer"), "client"),
    (("industr", "sector"), "industry_served"),
    (("contact", "email", "phone"), "contact"),
]

# Words that signal an office / branch / regional-coverage question (→ get_office).
_OFFICE_KEYWORDS = (
    "office", "branch", "cover", "covers", "region", "regional", "located", "location",
    "handle", "handles", "look after", "looks after", "head office", "headquarter", "head-quarter",
)
# Region words in the text → the region name get_office expects.
_REGIONS = {
    "north": "North", "northern": "North", "south": "South", "southern": "South",
    "east": "East", "eastern": "East", "west": "West", "western": "West",
}


def _kinds_for(question: str) -> list[str]:
    """Every company_fact kind the question touches (order-preserving, de-duplicated)."""
    q = question.lower()
    out: list[str] = []
    for keywords, kind in _KIND_KEYWORDS:
        if kind not in out and any(k in q for k in keywords):
            out.append(kind)
    return out


def _kind_for(question: str) -> str | None:
    kinds = _kinds_for(question)
    return kinds[0] if kinds else None


def _office_kwargs(conn, question: str) -> dict | None:
    """Resolve get_office kwargs from a location question, using the release's own place names.

    Prefers an explicit city, then a state, then a region word; a bare "head office" question
    resolves via the head-office fallback (empty kwargs). Returns ``None`` when the question is
    not location-shaped.
    """
    q = question.lower()
    if not any(k in q for k in _OFFICE_KEYWORDS) and "india" not in q:
        return None

    cities = conn.execute(_text("SELECT DISTINCT city FROM facts.active_office")).scalars().all()
    for city in cities:
        if city and city.lower() in q:
            return {"city": city}
    states = conn.execute(
        _text("SELECT DISTINCT state FROM facts.active_region_state")
    ).scalars().all()
    for state in states:
        if state and state.lower() in q:
            return {"state": state}
    for word, region in _REGIONS.items():
        if word in q:
            return {"region": region}
    if "head" in q or "headquarter" in q or "head-quarter" in q:
        return {}  # head-office fallback
    return None


def run(ctx, *, prompt_body, triage, history, latest_user, tools) -> AgentOutput:
    language = triage.get("language", "en")

    structured_recs = []
    kinds = _kinds_for(latest_user)  # multi-part: look up EVERY kind the question touches
    for kind in kinds:
        rec = tools.get_company_fact(kind)
        if rec.result.get("facts"):
            structured_recs.append(rec)

    got_office = False
    office_kwargs = _office_kwargs(ctx.conn, latest_user)
    if office_kwargs is not None:
        rec = tools.get_office(**office_kwargs)
        if rec.result.get("office"):
            got_office = True
            structured_recs.append(rec)

    # Structured-first: when a company fact or a specific office answered the question, the
    # structured result is the citation — skip the document search so the reply cites the office
    # / fact id, not an incidental catalogue chunk. BUT a machine/product/standard question (e.g.
    # "are your machines API-618 compliant?") is never in the company_fact table, so search the
    # catalogues even when a company-cert kind happened to match — never concede it unsearched.
    structured = bool(kinds) or got_office
    if not structured or _is_product_or_standard_question(latest_user):
        try:
            tools.search_documents(latest_user, k=3)
        except Exception:  # noqa: BLE001 - missing retrieval infra must not crash the agent
            pass

    def _search_fallback() -> None:
        # Absence protocol (LLD-AG): before conceding a factual sub-question, actually search.
        tools.search_documents(latest_user, k=3)

    message, citations = compose_grounded_answer(
        complete=ctx.complete,
        prompt_body=prompt_body,
        history=history,
        latest_user=latest_user,
        records=tools.records,
        language=language,
        search_fallback=_search_fallback,
        extra_instruction=(
            "The visitor may be asking several things at once — answer each part you can from the "
            "tool results, and don't let one missing part stop you answering the others."
        ),
    )
    # Force-cite the structured company-fact / office results that answered the question, so their
    # ids are grounded even if the compose LLM omits them (honest — they were used).
    for rec in structured_recs:
        if rec.tr_id not in citations:
            citations.append(rec.tr_id)
    return AgentOutput(
        action="answer",
        draft_text=message,
        citations=citations,
        output={"action": "answer", "kinds": kinds, "office": got_office},
    )
