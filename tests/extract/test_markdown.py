"""Section split (LLD-EXT-01): front-matter, H1–H3 breaks, page ranges. No DB."""

from __future__ import annotations

from agentkit.extract.markdown import parse_front_matter, split_sections

_MD = '''---
url: "https://www.jyotech.com/pdf/PROCESS.pdf"
kind: "pdf"
sha256: "abc"
fetched_at: "2026-08-23T00:00:00+00:00"
title: null
---

<!-- page 1 -->

# Products

Intro line.

## Process Compressors

Reciprocating, Non-Lubricated. Capacity up to 20000 Nm3/hr.

### Detail

deep content

<!-- page 2 -->

more detail on page two

## Oxygen Compressors

Up to 20000 Nm3/hr, water cooled.
'''


def test_front_matter_parsed_as_json_scalars() -> None:
    meta, body = parse_front_matter(_MD)
    assert meta["url"].endswith("PROCESS.pdf")
    assert meta["kind"] == "pdf"
    assert meta["title"] is None
    assert body.lstrip().startswith("<!-- page 1 -->")


def test_split_on_h1_h3_with_heading_paths() -> None:
    secs = split_sections(_MD, doc_id="doc.process", kind="pdf", url="u")
    paths = [s.heading_path for s in secs]
    assert ["Products"] in paths
    assert ["Products", "Process Compressors"] in paths
    assert ["Products", "Process Compressors", "Detail"] in paths
    assert ["Products", "Oxygen Compressors"] in paths  # H2 pops the H3


def test_deeper_section_text_and_ids_are_stable() -> None:
    secs = split_sections(_MD, doc_id="doc.process", kind="pdf", url="u")
    by_path = {tuple(s.heading_path): s for s in secs}
    proc = by_path[("Products", "Process Compressors")]
    assert "Capacity up to 20000 Nm3/hr" in proc.text
    assert proc.section_id.startswith("doc.process::s")


def test_page_range_tracks_markers() -> None:
    secs = split_sections(_MD, doc_id="doc.process", kind="pdf", url="u")
    by_path = {tuple(s.heading_path): s for s in secs}
    detail = by_path[("Products", "Process Compressors", "Detail")]
    # Detail starts on p1 and runs into p2.
    assert detail.page_start == 1
    assert detail.page_end == 2
    assert detail.locator.startswith("p1-2 §")


def test_evidence_text_includes_heading() -> None:
    # The heading lives in heading_path, not the body — but it is verbatim source,
    # so evidence_text (what the gate checks) must contain it.
    secs = split_sections(_MD, doc_id="doc.process", kind="pdf", url="u")
    by_path = {tuple(s.heading_path): s for s in secs}
    oxy = by_path[("Products", "Process Compressors")]
    assert "Process Compressors" not in oxy.text          # heading not in body
    assert "Process Compressors" in oxy.evidence_text      # but in evidence_text
    assert "Capacity up to 20000 Nm3/hr" in oxy.evidence_text  # body still included


def test_html_sections_have_no_page_range() -> None:
    html = "---\nurl: \"http://x/a.html\"\nkind: \"html\"\nsha256: \"z\"\nfetched_at: null\ntitle: null\n---\n\n# About\n\nISO 9001:2015 certified.\n"
    secs = split_sections(html, doc_id="doc.a", kind="html", url="http://x/a.html")
    assert len(secs) == 1
    assert secs[0].page_start is None
    assert secs[0].locator == "§About"
