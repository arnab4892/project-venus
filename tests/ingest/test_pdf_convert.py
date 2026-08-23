"""PDF → Markdown wrapper (LLD-ING-03), with Docling mocked.

We stub the page-render seam so tests are deterministic and never download
Docling models: assert `<!-- page N -->` markers, the < 50 char/page OCR
decision, and that OCR is only invoked when a low-density page exists.
"""

from __future__ import annotations

from agentkit.ingest.pdf_convert import OCR_DENSITY_THRESHOLD, Page, pdf_to_markdown


class _Render:
    """Records each call so we can assert whether an OCR pass happened."""

    def __init__(self, no_ocr, ocr=None):
        self._no_ocr = no_ocr
        self._ocr = ocr if ocr is not None else no_ocr
        self.calls = []

    def __call__(self, pdf_bytes, *, ocr):
        self.calls.append(ocr)
        return self._ocr if ocr else self._no_ocr


def test_page_markers_and_no_ocr_when_dense():
    pages = [
        Page(page_no=1, text="x" * 400, markdown="## Process\n\nReciprocating."),
        Page(page_no=2, text="y" * 400, markdown="## Fire & Safety\n\nBreathing air."),
    ]
    render = _Render(pages)
    res = pdf_to_markdown(b"%PDF-1.4 fake", render=render)

    assert "<!-- page 1 -->" in res.markdown
    assert "<!-- page 2 -->" in res.markdown
    assert res.page_count == 2
    assert res.low_density_pages == []
    assert res.pages_ocred == []
    assert render.calls == [False]  # no OCR pass at all


def test_low_density_page_triggers_ocr_only_for_that_page():
    no_ocr = [
        Page(page_no=1, text="x" * 400, markdown="## Dense page one"),
        Page(page_no=2, text="", markdown="<!-- image -->"),  # scanned, empty text
    ]
    ocr = [
        Page(page_no=1, text="x" * 400, markdown="## Dense page one"),
        Page(page_no=2, text="OCR recovered breathing air spec", markdown="OCR recovered breathing air spec"),
    ]
    render = _Render(no_ocr, ocr)
    res = pdf_to_markdown(b"%PDF-1.4 fake", render=render)

    assert res.low_density_pages == [2]
    assert res.pages_ocred == [2]
    assert render.calls == [False, True]  # dense pass, then OCR pass
    # page 2's markdown comes from the OCR pass; page 1 stays from the first pass
    assert "OCR recovered breathing air spec" in res.markdown
    assert "<!-- page 2 -->" in res.markdown


def test_threshold_is_fifty_chars_per_page():
    assert OCR_DENSITY_THRESHOLD == 50
    boundary = [
        Page(page_no=1, text="z" * 49, markdown="a"),   # below → OCR
        Page(page_no=2, text="z" * 50, markdown="b"),   # at threshold → not OCR
    ]
    render = _Render(boundary)
    res = pdf_to_markdown(b"x", render=render)
    assert res.low_density_pages == [1]


def test_tables_detected_in_markdown():
    pages = [
        Page(
            page_no=1,
            text="t" * 100,
            markdown="| A | B |\n| --- | --- |\n| 1 | 2 |",
        )
    ]
    res = pdf_to_markdown(b"x", render=_Render(pages))
    assert res.tables == 1
