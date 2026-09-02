"""Documents & Compliance agent (LLD-AG-03).

Serves catalogues/datasheets/certificates and verbatim company/compliance facts. Tools:
``search_documents`` (04), ``get_company_fact`` (05), ``get_office`` (06).

* A **download / catalogue** ask (the catalogue-download question class) emits one
  ``document_card`` message (``kind="document_card"``, ``payload={title, url, locator}``) **per
  distinct document the answer actually references** (deduped by document id, in retrieval order,
  capped) — a specific single-catalogue ask yields one card, a generic "what catalogues do you
  have" yields one per catalogue — alongside a short grounded text intro. The card's title comes
  from ``facts.document.title`` when published, else a readable label derived from the document URL
  (the live PDF catalogues carry a null title).
* A **company / compliance** ask (certifications, founding, coverage, facilities…) is answered
  verbatim from ``get_company_fact``.

Retrieval query is English even for a Hindi/Hinglish message (``english_query``, LLD-RT-07);
the reply is in the visitor's language. The card rides the answer path, so a failed grounding
gate drops it with the drafted text (LLD-RT-05).
"""

from __future__ import annotations

from agentkit.runtime.agents.base import AgentOutput, compose_grounded_answer
from agentkit.runtime.agents.faq_company import _kind_for
from agentkit.runtime.grounding import used_documents
from agentkit.runtime.language import english_query
from agentkit.runtime.ops import MessageRecord

# The URL→title fallback now lives with the sources resolver; re-exported here so callers
# (and the download-card builder below) keep importing it from this module.
from agentkit.runtime.sources import title_from_url

# At most this many download cards on a single answer (a generic catalogue ask).
_MAX_CARDS = 3

# Words that signal the visitor wants a downloadable document (→ a document_card).
_DOC_KEYWORDS = (
    "catalog", "catalogue", "download", "datasheet", "data sheet", "brochure", "leaflet",
    "pdf", "document", "spec sheet", "spec-sheet", "manual",
)
# A download card is only for an actual downloadable FILE — a retrieved HTML page (an about/index
# page that merely lists the catalogues) is not something to hand over as a download.
_DOWNLOADABLE_EXTS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip")


def _wants_document(text: str) -> bool:
    q = text.lower()
    return any(k in q for k in _DOC_KEYWORDS)


def _is_downloadable(url: str | None) -> bool:
    return bool(url) and url.lower().split("?", 1)[0].rstrip("/").endswith(_DOWNLOADABLE_EXTS)


def _document_card(chunk: dict) -> MessageRecord:
    """Build a document_card message from a search hit (title/url/locator)."""
    return MessageRecord(
        role="assistant",
        kind="document_card",
        payload={
            "title": chunk.get("title") or title_from_url(chunk.get("url")),
            "url": chunk.get("url"),
            "locator": chunk.get("locator"),
        },
    )


def run(ctx, *, prompt_body, triage, history, latest_user, tools) -> AgentOutput:
    language = triage.get("language", "en")

    kind = _kind_for(latest_user)
    if kind is not None:
        tools.get_company_fact(kind)

    # Document search (best-effort; translated to English for retrieval). A generic catalogue ask
    # can span several documents, so retrieve enough to surface each distinct catalogue.
    query = english_query(ctx.complete, latest_user, language)
    chunks: list[dict] = []
    try:
        # k is generous so a generic "what catalogues do you have" surfaces every distinct
        # catalogue, not just the top-ranked one; used_documents then keeps only those referenced.
        rec = tools.search_documents(query, k=10)
        chunks = rec.result.get("chunks", []) or []
    except Exception:  # noqa: BLE001 - missing retrieval infra ≠ a failure
        pass

    message, citations = compose_grounded_answer(
        complete=ctx.complete,
        prompt_body=prompt_body,
        history=history,
        latest_user=latest_user,
        records=tools.records,
        language=language,
        extra_instruction=(
            "State company facts verbatim from the tool results. If the visitor asked for "
            "downloadable documents, point them to the matching catalogue(s) by name — a download "
            "card is shown for each catalogue you name (say 'a card below' when it is one, 'cards "
            "below' when several). Only name a catalogue you are actually pointing them to."
        ),
    )

    # One card PER DISTINCT DOWNLOADABLE DOCUMENT the answer actually references (dedup by doc,
    # retrieval order, capped) — so prose and cards agree: a specific single-document ask yields one
    # card, a generic "what catalogues do you have" yields one per catalogue, and a retrieved HTML
    # listing page never becomes a bogus "download". Cards ride the grounded answer path
    # (extra_messages), so a failed grounding gate drops them with the draft (LLD-AG-03); each
    # referenced document is cited by the same used_documents derivation in the gate.
    extra_messages: list[MessageRecord] = []
    if _wants_document(latest_user) and chunks:
        downloadable = [c for c in used_documents(chunks, message) if _is_downloadable(c.get("url"))]
        extra_messages = [_document_card(c) for c in downloadable[:_MAX_CARDS]]

    return AgentOutput(
        action="answer",
        draft_text=message,
        citations=citations,
        extra_messages=extra_messages,
        output={"action": "answer", "kind": kind, "served_cards": len(extra_messages)},
    )
