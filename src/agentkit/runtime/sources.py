"""Customer-facing sources resolver (PRD-F-008 presentation; LLD-RT-05 rider).

A turn's citations are developer-shaped ids (``cap.*`` / ``fam.*`` / ``ch.*``) and carry a
``url`` only on ``search_documents`` chunks — every other kind arrives ``url=None``. This
module turns that raw citation list into a **customer-ready** Sources list: every citation is
walked to its source document via the ``facts.active_*`` views, grouped so each distinct
document appears once, with a human ``title`` (from ``facts.active_document`` — the existing
URL-derived fallback fills a null title), a readable ``location`` (page + section for PDFs, the
section label for web pages) and a ``link`` (a PDF gets a ``#page=N`` anchor from the locator;
a web page is its own url; a null url renders no link).

``resolve_sources`` reads only the read-only ``facts.*`` views — it never writes. The runtime
persists its output into the answer message payload (``ops.message.payload.sources``) so the
conversation record keeps *what the customer saw*; the raw ``ops.citation`` rows are untouched.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from sqlalchemy import Connection, text


def title_from_url(url: str | None) -> str:
    """A readable document label from its URL basename (decoded, extension stripped).

    Relocated here from ``runtime.agents.documents_compliance`` so both the download-card
    builder and the sources resolver share one URL→title fallback.
    """
    if not url:
        return "Document"
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    for ext in (".pdf", ".html", ".php", ".htm"):
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
    return name.strip() or "Document"


# Citation kinds whose row carries a ``source_doc_id`` + ``source_locator`` in its active_* view.
# (chunk resolves via ``doc_id``; document IS the doc — both handled specially below.)
_KIND_SOURCE: dict[str, tuple[str, str]] = {
    "capability": ("active_capability_row", "cap_id"),
    "family": ("active_product_family", "family_id"),
    "product": ("active_product", "product_id"),
    "fact": ("active_company_fact", "fact_id"),
    "office": ("active_office", "office_id"),
}

# A locator looks like ``p4-5 §PROCESS COMPRESSORS`` (PDF) or ``§Certifications`` /
# ``§Process Compressors`` (web / page-less) — an optional ``pN``/``pN-M`` page prefix, then the
# heading after ``§``. Some facts locators prefix a doc-name instead of a page (``PROCESS.pdf §…``).
_PAGE_RE = re.compile(r"p(\d+)(?:\s*[-–]\s*(\d+))?", re.IGNORECASE)


def _cval(citation, field: str):
    """Read a field from either a ``CitationRecord`` or a plain ``{kind, ref_id, ...}`` dict."""
    if isinstance(citation, dict):
        return citation.get(field)
    return getattr(citation, field, None)


def _parse_locator(loc: str | None) -> tuple[int | None, int | None, str | None]:
    """``(page_start, page_end, heading)`` from a locator; any part may be ``None``."""
    if not loc:
        return (None, None, None)
    s = loc.strip()
    ps = pe = None
    m = _PAGE_RE.match(s)
    if m:
        ps = int(m.group(1))
        pe = int(m.group(2)) if m.group(2) else None
        s = s[m.end():].strip()
    heading = (s.rpartition("§")[2] if "§" in s else s).strip() or None
    return (ps, pe, heading)


def _best_locator(locs: list[str]) -> str | None:
    """The most specific locator for a document: prefer one carrying a page, then a heading."""
    best: str | None = None
    best_key: tuple[int, int] = (-1, -1)
    for loc in locs:
        ps, _, heading = _parse_locator(loc)
        key = (1 if ps else 0, 1 if heading else 0)
        if key > best_key:
            best_key, best = key, loc
    return best


def _location(is_pdf: bool, ps: int | None, pe: int | None, heading: str | None) -> str | None:
    """Readable location: ``p. 4–5 — Process Compressors`` for a paged PDF, else the section label."""
    if is_pdf and ps:
        page = f"p. {ps}" + (f"–{pe}" if pe and pe != ps else "")
        return f"{page} — {heading}" if heading else page
    return heading


def _link(is_pdf: bool, url: str | None, ps: int | None) -> str | None:
    """The link a source row points at: PDF + page → ``url#page=N``; web → the url; no url → None."""
    if not url:
        return None
    return f"{url}#page={ps}" if is_pdf and ps else url


def resolve_sources(conn: Connection, citations) -> list[dict]:
    """Turn a turn's citations into a customer-ready sources list.

    Returns ``[{title, url, link, location, kind}]`` — one entry per distinct source document,
    in first-cited order. ``kind`` is ``"pdf"`` or ``"web"``. Reads only ``facts.*`` views.
    """
    reqs: list[tuple[str, str, str | None]] = []  # (kind, ref_id, citation-supplied locator)
    by_kind: dict[str, set[str]] = {}
    for c in citations or []:
        kind, ref = _cval(c, "kind"), _cval(c, "ref_id")
        if not ref:
            continue
        reqs.append((kind, ref, _cval(c, "locator")))
        by_kind.setdefault(kind, set()).add(ref)
    if not reqs:
        return []

    # (kind, ref_id) -> (doc_id, source_locator)
    resolved: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    for ref in by_kind.get("document", ()):  # a document citation IS its own doc
        resolved[("document", ref)] = (ref, None)
    if by_kind.get("chunk"):  # a chunk resolves to its parent document by doc_id
        for r in conn.execute(
            text("SELECT chunk_id AS id, doc_id, locator FROM facts.active_chunk "
                 "WHERE chunk_id = ANY(:ids)"),
            {"ids": list(by_kind["chunk"])},
        ).mappings():
            resolved[("chunk", r["id"])] = (r["doc_id"], r["locator"])
    for kind, (view, idcol) in _KIND_SOURCE.items():  # capability / family / product / fact / office
        ids = by_kind.get(kind)
        if not ids:
            continue
        for r in conn.execute(
            text(f"SELECT {idcol} AS id, source_doc_id, source_locator FROM facts.{view} "
                 f"WHERE {idcol} = ANY(:ids)"),
            {"ids": list(ids)},
        ).mappings():
            resolved[(kind, r["id"])] = (r["source_doc_id"], r["source_locator"])

    # group by document, first-cited order, collecting candidate locators
    order: list[str] = []
    cand: dict[str, list[str]] = {}
    for kind, ref, cite_loc in reqs:
        doc_id, src_loc = resolved.get((kind, ref), (None, None))
        if not doc_id:
            continue
        if doc_id not in cand:
            cand[doc_id] = []
            order.append(doc_id)
        loc = cite_loc or src_loc
        if loc:
            cand[doc_id].append(loc)
    if not order:
        return []

    docmeta: dict[str, tuple[str | None, str | None, str | None]] = {}
    for d in conn.execute(
        text("SELECT doc_id AS id, kind, title, url FROM facts.active_document "
             "WHERE doc_id = ANY(:ids)"),
        {"ids": order},
    ).mappings():
        docmeta[d["id"]] = (d["kind"], d["title"], d["url"])

    out: list[dict] = []
    for doc_id in order:
        doc_kind, title, url = docmeta.get(doc_id, (None, None, None))
        is_pdf = doc_kind == "pdf"
        ps, pe, heading = _parse_locator(_best_locator(cand.get(doc_id, [])))
        out.append({
            "title": title or title_from_url(url),
            "url": url,
            "link": _link(is_pdf, url, ps),
            "location": _location(is_pdf, ps, pe, heading),
            "kind": "pdf" if is_pdf else "web",
        })
    return out
