"""HTML → Markdown conversion (LLD-ING-02).

Fixture-driven, no network: selector stripping, table → pipe table, preserved
PDF links, image alt, and heading/table counts for the report.
"""

from __future__ import annotations

from pathlib import Path

from agentkit.ingest.html_convert import html_to_markdown

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://www.jyotech.com/process-gas-compressors.html"

# The real derived selector list (see clients/jyotech/seeds/sources.yaml).
STRIP = [
    "nav", "header", "script", "style", "noscript", "iframe",
    ".container-fluid.bg-primary", ".container-fluid.bg-dark",
    ".container-fluid.text-white", ".floating-buttons", ".back-to-top", ".modal",
]


def _convert():
    html = (FIXTURES / "sample.html").read_bytes()
    return html_to_markdown(html, strip_selectors=STRIP, base_url=BASE_URL)


def test_chrome_is_stripped():
    md = _convert().markdown
    for gone in [
        "+91-120-4711300",       # top contact bar
        "Facebook", "LinkedIn", "WhatsApp",  # social bars
        "All Rights Reserved",   # copyright bar
        "console.log",           # <script>
        "maps/embed",            # maps <iframe>
        "footer-about",          # footer band
        "Spinner Start",         # HTML comment must not leak in as text
    ]:
        assert gone not in md, f"expected {gone!r} stripped, got:\n{md}"


def test_headings_and_content_survive():
    res = _convert()
    assert "# PROCESS GAS COMPRESSORS" in res.markdown
    assert "## Envelope" in res.markdown
    assert res.headings == 2  # h1 + h2


def test_table_becomes_pipe_table():
    md = _convert().markdown
    assert "| Parameter | Min | Max |" in md
    assert "| --- | --- | --- |" in md
    assert "| Capacity (Nm³/hr) | 100 | 20000 |" in md
    assert _convert().tables == 1


def test_pdf_link_is_preserved_and_absolutised():
    md = _convert().markdown
    assert (
        "[Download catalogue]"
        "(https://www.jyotech.com/pdf/Jyotech%20Catalog%20-%20PROCESS.pdf)"
    ) in md


def test_image_alt_text_kept():
    assert "Reciprocating compressor skid" in _convert().markdown


def test_title_extracted():
    assert _convert().title == "Process Gas Compressors"


def test_nm3_survives_conversion():
    # ³ (U+00B3) must not be mangled during HTML parsing.
    assert "Nm³/hr" in _convert().markdown
