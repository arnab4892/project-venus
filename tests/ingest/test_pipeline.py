"""End-to-end ingestion pipeline (LLD-ING-01/04/05) — network + Docling mocked.

Drives `run_ingest` over a tiny in-memory `Sources` (one HTML page + one PDF)
with an httpx MockTransport and a stubbed PDF render. Asserts: files written on
first run, **nothing written on the second run** (hash idempotency), front-matter
fields, and `Nm³/hr` intact in the written HTML markdown. DB-free (`conn=None`).
"""

from __future__ import annotations

from pathlib import Path

import httpx

from agentkit.ingest.pdf_convert import Page
from agentkit.ingest.pipeline import run_ingest
from agentkit.ingest.sources import Sources

FIXTURES = Path(__file__).parent / "fixtures"
PAGE_URL = "https://www.jyotech.com/process-gas-compressors.html"
PDF_URL = "https://www.jyotech.com/pdf/Jyotech%20Catalog%20-%20PROCESS.pdf"

STRIP = [
    "nav", "header", "script", "style", "noscript", "iframe",
    ".container-fluid.bg-primary", ".container-fluid.bg-dark",
    ".container-fluid.text-white", ".floating-buttons", ".back-to-top", ".modal",
]


def _sources() -> Sources:
    return Sources(
        client="jyotech",
        start_url=PAGE_URL,
        crawl={"same_host_only": True, "max_depth": 2, "delay_seconds": 0},
        pages=[PAGE_URL],
        pdfs=[PDF_URL],
        exclude=[],
        strip_selectors=STRIP,
    )


def _mock_client() -> httpx.Client:
    html = (FIXTURES / "sample.html").read_bytes()
    pdf = (FIXTURES / "tiny.pdf").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == PAGE_URL:
            return httpx.Response(200, content=html, headers={"content-type": "text/html"})
        if url == PDF_URL:
            return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _pdf_render(pdf_bytes, *, ocr):
    return [Page(page_no=1, text="Reciprocating compressor. " * 10, markdown="## Process\n\nUp to 20,000 Nm³/hr.")]


def _run(tmp: Path):
    with _mock_client() as client:
        return run_ingest(
            "jyotech",
            sources=_sources(),
            http_client=client,
            conn=None,               # DB-free
            data_root=tmp,
            pdf_render=_pdf_render,
        )


def test_first_run_writes_both_sources(tmp_path):
    report = _run(tmp_path)
    assert len(report.written) == 2
    assert report.skipped == []
    assert report.errors == []
    md_files = list((tmp_path / "jyotech" / "md").glob("*.md"))
    assert len(md_files) == 2


def test_second_run_is_idempotent_writes_nothing(tmp_path):
    _run(tmp_path)
    before = {p: p.stat().st_mtime_ns for p in (tmp_path / "jyotech" / "md").glob("*.md")}

    report = _run(tmp_path)
    assert report.written == []
    assert len(report.skipped) == 2

    after = {p: p.stat().st_mtime_ns for p in (tmp_path / "jyotech" / "md").glob("*.md")}
    assert before == after  # no file rewritten


def test_written_html_has_front_matter_and_intact_nm3(tmp_path):
    _run(tmp_path)
    md_dir = tmp_path / "jyotech" / "md"
    htmls = [p for p in md_dir.glob("*.md") if "html" in p.read_text()]
    assert htmls, "expected an HTML markdown output"
    text = htmls[0].read_text()
    assert text.startswith("---\n")
    for key in ("url:", "kind:", "sha256:", "fetched_at:", "title:"):
        assert key in text
    assert "Nm³/hr" in text
    assert "Nm3/hr" not in text  # not folded


def test_missing_url_is_recorded_not_fatal(tmp_path):
    src = _sources()
    src.pages = [PAGE_URL, "https://www.jyotech.com/does-not-exist.html"]
    with _mock_client() as client:
        report = run_ingest(
            "jyotech", sources=src, http_client=client, conn=None,
            data_root=tmp_path, pdf_render=_pdf_render,
        )
    assert any("does-not-exist" in e.url for e in report.errors)
    # the good sources still converted
    assert len(report.written) == 2
