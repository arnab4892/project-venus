"""FAQ / Company agent (LLD-AG-06, and the company-facts slice of LLD-AG-03).

Answers company questions (certifications, founding, facilities, coverage, clients, industries,
contacts) and general "about us" / document questions. It fetches ``get_company_fact`` for the
kind implied by the question and augments with a ``search_documents`` fallback, then composes a
grounded answer citing the tool-result ids (locator + url, PRD-F-008). Every number/fact is
sourced; if nothing relevant is found the grounding gate falls the reply back honestly.
"""

from __future__ import annotations

from agentkit.runtime.agents.base import AgentOutput, compose_grounded_answer

# Question keyword → company_fact kind (checked in order; first hit wins).
_KIND_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("certif", "iso"), "certification"),
    (("founder",), "founder"),
    (("found", "establish", "since", "year"), "founded"),
    (("facilit", "manufactur", "plant", "factory"), "facility"),
    (("coverage", "country", "countries", "export", "region", "serve"), "coverage"),
    (("client", "customer"), "client"),
    (("industr", "sector"), "industry_served"),
    (("contact", "email", "phone"), "contact"),
]


def _kind_for(question: str) -> str | None:
    q = question.lower()
    for keywords, kind in _KIND_KEYWORDS:
        if any(k in q for k in keywords):
            return kind
    return None


def run(ctx, *, prompt_body, triage, history, latest_user, tools) -> AgentOutput:
    language = triage.get("language", "en")

    kind = _kind_for(latest_user)
    if kind is not None:
        tools.get_company_fact(kind)

    # Document search fallback (best-effort — missing retrieval infra must not crash the agent).
    try:
        tools.search_documents(latest_user, k=3)
    except Exception:  # noqa: BLE001
        pass

    message, citations = compose_grounded_answer(
        complete=ctx.complete,
        prompt_body=prompt_body,
        history=history,
        latest_user=latest_user,
        records=tools.records,
        language=language,
    )
    return AgentOutput(
        action="answer",
        draft_text=message,
        citations=citations,
        output={"action": "answer", "kind": kind},
    )
