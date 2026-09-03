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

# Fixed, per-language fallback sentences shipped when a draft fails the grounding gate
# (LLD-RT-05, LLD-RT-07). Deterministic and pre-vetted — never LLM-translated at runtime,
# because the fallback fires exactly when the model's own output has just failed the gate.
# Technical/model terms and units stay English even in the hi/hinglish register (persona rule).
_FALLBACK_TEXTS = {
    "en": (
        "I want to make sure I only give you figures we actually publish, and I don't have that "
        "one in front of me. Let me put you with our engineers who can confirm the exact details — "
        "shall I?"
    ),
    "hi": (
        "मैं आपको सिर्फ़ वही आँकड़े देना चाहता हूँ जो हम वाकई प्रकाशित करते हैं, और वो अभी मेरे सामने नहीं है। "
        "मैं आपको हमारे engineers से जोड़ देता हूँ जो सटीक जानकारी की पुष्टि कर सकते हैं — क्या मैं ऐसा करूँ?"
    ),
    "hinglish": (
        "Main aapko sirf wahi figures dena chahta hoon jo hum actually publish karte hain, aur woh "
        "abhi mere saamne nahi hai. Main aapko hamare engineers se jod deta hoon jo exact details "
        "confirm kar sakte hain — kya main aisa karun?"
    ),
}
# Back-compat alias: the English fallback is the historical single-string constant.
FALLBACK_TEXT = _FALLBACK_TEXTS["en"]


def fallback_text(language: str | None) -> str:
    """The gate-strip fallback sentence in the visitor's language (defaults to English)."""
    return _FALLBACK_TEXTS.get((language or "en").lower(), _FALLBACK_TEXTS["en"])


# Devanagari digits (०-९, U+0966–U+096F) fold 1:1 to ASCII. Applied before every numeric match
# so a Devanagari figure can never slip past the numeric guard (the ASCII-digit persona rule is
# the intended behaviour; this is the safety net). Being 1:1 single code points, str.translate
# preserves every offset, so redaction substitution stays aligned and the output normalises to ASCII.
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def _fold_digits(text: str) -> str:
    return (text or "").translate(_DEVANAGARI_DIGITS)


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
    return {_to_float(m.group(0)) for m in _NUMBER_RE.finditer(_fold_digits(text))}


def _spec_numbers(text: str) -> set[float]:
    cleaned = _CITATION_MARKER_RE.sub(" ", _fold_digits(text))
    return {_to_float(m.group(1)) for m in _SPEC_RE.finditer(cleaned)}


def spec_numbers(text: str) -> set[float]:
    """Public: the *spec* numbers (adjacent to a capacity/pressure/measure unit) in ``text``."""
    return _spec_numbers(text)


def numeric_universe(tool_records, user_texts) -> set[float]:
    """Every number the reply is allowed to contain: tool results/args + the visitor's own text."""
    allowed: set[float] = set()
    for rec in tool_records:
        allowed |= _all_numbers(json.dumps(rec.result, default=str))
        allowed |= _all_numbers(json.dumps(rec.args, default=str))
    for t in user_texts:
        allowed |= _all_numbers(t or "")
    return allowed


def redact_unsourced_spec_numbers(text: str, allowed: set[float]) -> tuple[str, list[float]]:
    """Strip any *spec* figure not in ``allowed`` from ``text`` (LLD-RT-05, every message).

    A number adjacent to a capacity/pressure/measure unit that is neither in this turn's tool
    results nor in the visitor's own message is a fabrication (typically parroted from a prompt
    exemplar) — it is replaced with an ellipsis, wherever it appears and whatever the action.
    Returns ``(clean_text, stripped_numbers)``.
    """
    stripped: list[float] = []

    def _repl(m: re.Match) -> str:
        num = _to_float(m.group(1))
        if num in allowed:
            return m.group(0)
        stripped.append(num)
        return "…"

    cleaned = _SPEC_RE.sub(_repl, _CITATION_MARKER_RE.sub(" ", _fold_digits(text)))
    return cleaned, stripped


def all_numbers(text: str) -> set[float]:
    """Public: every number in ``text``."""
    return _all_numbers(text)


_TOKEN_RE = re.compile(r"[a-z]{5,}|api[\s\-]?\d+|\d[\d,\.]*")


def _best_chunk(chunks: list[dict], answer_text: str | None) -> dict:
    """The retrieved chunk the answer actually drew from — not blindly the top RRF hit.

    Hybrid ranking can float an off-topic chunk to #1 while the answer is composed from a
    lower-ranked one (e.g. an API-618 question whose top hit is a PPV-blower page, but whose
    answer comes from the process catalogue at rank 2). Pick the chunk sharing the most
    distinctive tokens with the answer; on a tie or no answer text, keep the top hit.
    """
    if answer_text is None or len(chunks) == 1:
        return chunks[0]
    low = answer_text.lower().replace(",", "")
    ans = {t.replace(",", "") for t in _TOKEN_RE.findall(low)}

    def _overlap(c: dict) -> int:
        toks = {t.replace(",", "") for t in _TOKEN_RE.findall((c.get("content_md") or "").lower())}
        return len(toks & ans)

    best = max(range(len(chunks)), key=lambda i: (_overlap(chunks[i]), -i))
    return chunks[best] if _overlap(chunks[best]) > 0 else chunks[0]


def _answer_tokens(answer_text: str | None) -> set[str]:
    low = (answer_text or "").lower().replace(",", "")
    return {t.replace(",", "") for t in _TOKEN_RE.findall(low)}


def _chunk_tokens(chunk: dict) -> set[str]:
    blob = f"{chunk.get('content_md') or ''} {chunk.get('title') or ''}".lower()
    return {t.replace(",", "") for t in _TOKEN_RE.findall(blob)}


# A document counts as referenced only if the answer shares at least this many distinctive tokens
# with it — one incidental word (a shared "catalogue"/"compressors") is not a reference.
_MIN_DOC_OVERLAP = 2


def used_documents(chunks: list[dict], answer_text: str | None, *, cap: int | None = None) -> list[dict]:
    """Distinct documents (by ``doc_id``) whose chunk content the answer actually used.

    One representative chunk per document — the highest-overlap chunk — in retrieval order. This
    is the multi-document generalisation of :func:`_best_chunk`: a generic "what catalogues do you
    have" answer references several documents and each is surfaced (a card, a citation); a specific
    single-document answer yields exactly one.

    A document is *used* when the answer shares at least ``_MIN_DOC_OVERLAP`` distinctive tokens
    with its chunks — one incidental shared word is not enough. When nothing clears the bar (or the
    answer is ``None``, or a single document was retrieved), fall back to the single best chunk —
    never zero the source. ``cap`` bounds the list for presentation; citation derivation passes no
    cap.
    """
    if not chunks:
        return []
    ans = _answer_tokens(answer_text)
    best_by_doc: dict = {}
    order: list = []
    for c in chunks:
        d = c.get("doc_id")
        overlap = len(_chunk_tokens(c) & ans)
        if d not in best_by_doc:
            best_by_doc[d] = (overlap, c)
            order.append(d)
        elif overlap > best_by_doc[d][0]:
            best_by_doc[d] = (overlap, c)
    used = [best_by_doc[d][1] for d in order if best_by_doc[d][0] >= _MIN_DOC_OVERLAP]
    if not used:
        used = [_best_chunk(chunks, answer_text)]
    return used[:cap] if cap else used


def _value_used(value, answer_text: str | None) -> bool:
    """True if a company-fact value is actually reflected in the answer (citation hygiene).

    ``get_company_fact(kind)`` can return many rows (e.g. six 'founded' years); only the rows the
    answer actually used should be cited. A row counts as used if any of its ≥4-char words or its
    numbers appear in the answer. ``answer_text=None`` keeps everything (direct unit-test callers).
    """
    if answer_text is None:
        return True
    low = answer_text.lower()
    tokens = re.findall(r"[a-z]{4,}|\d[\d,]*", str(value or "").lower())
    return any(t.replace(",", "") in low.replace(",", "") for t in tokens)


def _citations_from_record(rec: ToolCallRecord, answer_text: str | None = None) -> list[CitationRecord]:
    """Map a cited tool result to its citation rows (locator/url where published).

    Citations include the cited result **and its honest parent** — a matched capability row's
    family, a cited chunk's source document — so a golden/e2e expectation can be pinned by the
    stable document/family id, not only a renumbering chunk id. This is parent-derivation of a
    *cited* result, never speculative attachment of an unused source.
    """
    out: list[CitationRecord] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, ref_id, *, locator=None, url=None) -> None:
        if not ref_id or (kind, ref_id) in seen:
            return
        seen.add((kind, ref_id))
        out.append(CitationRecord(kind=kind, ref_id=ref_id, locator=locator, url=url))

    r = rec.result
    if rec.tool == "match_capability":
        for m in r.get("matches", []):
            add("capability", m["cap_id"])
            add("family", m.get("family_id"))  # parent family of the matched row
    elif rec.tool == "search_documents":
        # Cite each DISTINCT document the answer actually used (not just the single best chunk) —
        # a generic catalogue answer legitimately draws on several. Single-document answers still
        # cite exactly one (used_documents collapses to _best_chunk).
        for c in used_documents(r.get("chunks", []), answer_text):
            add("chunk", c["chunk_id"], locator=c.get("locator"), url=c.get("url"))
            add("document", c.get("doc_id"), locator=c.get("locator"), url=c.get("url"))
    elif rec.tool == "get_company_fact":
        used = [f for f in r.get("facts", []) if _value_used(f.get("value"), answer_text)]
        # If none obviously matched (paraphrase), fall back to citing all — never zero the source.
        for f in (used or r.get("facts", [])):
            add("fact", f["fact_id"], locator=f.get("source_locator"))
            add("document", f.get("source_doc_id"), locator=f.get("source_locator"))  # its source
    elif rec.tool in ("get_product", "list_products"):
        for p in r.get("products", []):
            add("product", p.get("product_id"), locator=p.get("source_locator"))
            add("family", p.get("family_id"))  # parent family of the product
            add("document", p.get("source_doc_id"), locator=p.get("source_locator"))  # its source doc
        for fam in r.get("families", []):  # list_products groups by family
            add("family", fam.get("family_id"))
    elif rec.tool == "get_office":
        office = r.get("office")
        if office:
            add("office", office["office_id"])
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
    language: str | None = "en",
) -> GroundingResult:
    """Apply the citation + numeric gate to a drafted answer.

    Returns a :class:`GroundingResult`: on pass, the original text + derived citation rows and
    ``status="full"``; on fail, the fallback text (in the visitor's ``language``, LLD-RT-07),
    no citations and ``status="none"``.
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
            citations.extend(_citations_from_record(by_id[tid], answer_text))
        return GroundingResult(
            ok=True,
            text=answer_text,
            citations=citations,
            grounding={"claims": claims, "grounded": claims, "status": "full"},
        )

    return GroundingResult(
        ok=False,
        text=fallback_text(language),
        citations=[],
        grounding={
            "claims": claims,
            "grounded": 0,
            "status": "none",
            "reason": "uncited" if not cited else f"unsourced_numbers:{sorted(offending)}",
        },
    )
