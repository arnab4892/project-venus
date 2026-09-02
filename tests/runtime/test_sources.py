"""Customer-facing sources resolver (PRD-F-008 presentation / LLD-RT-05 rider).

Every citation is walked to its source document via the read-only ``facts.active_*`` views and
grouped so each document appears once, with a customer-ready title / location / link. These are
integration tests against the demo seed (``seeded_conn``); concrete ids come from
``clients/jyotech/seeds/demo.yaml`` (doc.process is a PDF, doc.about/doc.contact are web pages).
"""

from __future__ import annotations

from sqlalchemy import text

from agentkit.runtime.sources import resolve_sources


def _active_release(conn) -> str:
    return conn.execute(text("SELECT release_id FROM facts.active_release")).scalar_one()


def test_empty_citations() -> None:
    # No DB access needed for the empty case.
    assert resolve_sources(conn=None, citations=[]) == []  # type: ignore[arg-type]


def test_group_dedupe_and_parent_resolution(seeded_conn) -> None:
    # capability + family both live in doc.process; a company fact in doc.about; an office in
    # doc.contact → three grouped entries, in first-cited order, doc.process appearing once.
    citations = [
        {"kind": "capability", "ref_id": "cap.001", "locator": None, "url": None},
        {"kind": "family", "ref_id": "fam.process_recip", "locator": None, "url": None},
        {"kind": "fact", "ref_id": "cf.001", "locator": "§Certifications", "url": None},
        {"kind": "office", "ref_id": "off.noida", "locator": None, "url": None},
    ]
    out = resolve_sources(seeded_conn, citations)

    titles = [s["title"] for s in out]
    assert titles == [
        "Catalogue – Industrial Compressors & Process Engineering Eqpt",  # doc.process (cap+fam)
        "About Us",  # doc.about (fact)
        "Contact Us",  # doc.contact (office)
    ]
    # capability with no locator still resolves to its parent document (doc.process, a PDF).
    assert out[0]["kind"] == "pdf"
    assert out[0]["url"].endswith("PROCESS.pdf")


def test_pdf_page_anchor_from_locator(seeded_conn) -> None:
    # A citation-supplied page locator drives 'p. N–M — Section' and the '#page=N' link anchor.
    out = resolve_sources(
        seeded_conn,
        [{"kind": "capability", "ref_id": "cap.001", "locator": "p4-5 §Oxygen Compressors",
          "url": None}],
    )
    assert len(out) == 1
    entry = out[0]
    assert entry["kind"] == "pdf"
    assert entry["location"] == "p. 4–5 — Oxygen Compressors"
    assert entry["link"] == entry["url"] + "#page=4"


def test_web_page_section_label_and_plain_link(seeded_conn) -> None:
    # A web page (doc.about) uses the section name as the location and its own url as the link.
    out = resolve_sources(
        seeded_conn, [{"kind": "fact", "ref_id": "cf.001", "locator": "§Certifications", "url": None}]
    )
    assert len(out) == 1
    entry = out[0]
    assert entry["kind"] == "web"
    assert entry["location"] == "Certifications"
    assert entry["link"] == entry["url"]  # no #page anchor for a web page
    assert "#page=" not in entry["link"]


def test_null_title_falls_back_to_url_derived_name(seeded_conn) -> None:
    rid = _active_release(seeded_conn)
    seeded_conn.execute(
        text("INSERT INTO facts.document (doc_id, release_id, kind, title, url) "
             "VALUES ('doc.tmp_notitle', :r, 'pdf', NULL, "
             "'https://www.jyotech.com/pdf/Widget Manual.pdf')"),
        {"r": rid},
    )
    out = resolve_sources(seeded_conn, [{"kind": "document", "ref_id": "doc.tmp_notitle"}])
    assert out[0]["title"] == "Widget Manual"  # derived from the URL basename
    assert out[0]["link"] == "https://www.jyotech.com/pdf/Widget Manual.pdf"


def test_null_url_renders_without_link(seeded_conn) -> None:
    rid = _active_release(seeded_conn)
    seeded_conn.execute(
        text("INSERT INTO facts.document (doc_id, release_id, kind, title, url) "
             "VALUES ('doc.tmp_nourl', :r, 'html', NULL, NULL)"),
        {"r": rid},
    )
    out = resolve_sources(seeded_conn, [{"kind": "document", "ref_id": "doc.tmp_nourl"}])
    assert out[0]["url"] is None
    assert out[0]["link"] is None  # no url → no link (defensive; today's corpus has none)
    assert out[0]["title"] == "Document"  # URL-derived fallback with no url
