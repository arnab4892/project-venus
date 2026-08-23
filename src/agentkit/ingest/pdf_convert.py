"""PDF → Markdown via Docling (LLD-ING-03).

Docling runs with table-structure recognition on. OCR is enabled *only* for
pages whose text density is below :data:`OCR_DENSITY_THRESHOLD` chars/page: we
first render without OCR, measure per-page text, and re-render with OCR only
when a low-density (likely scanned) page is present — using the OCR output for
those pages and the faster non-OCR output for the rest. Every page is separated
by a ``<!-- page N -->`` marker.

The page-render step is injected (`render`) so tests stub it and never download
Docling models.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

OCR_DENSITY_THRESHOLD = 50  # chars/page; below this a page is treated as scanned

_TABLE_SEP = re.compile(r"^\s*\|(?:\s*:?-{3,}:?\s*\|)+\s*$", re.MULTILINE)


@dataclass
class Page:
    page_no: int
    text: str
    markdown: str


@dataclass
class PdfResult:
    markdown: str
    page_count: int
    pages_ocred: list[int]
    low_density_pages: list[int]
    tables: int


def render_pages(pdf_bytes: bytes, *, ocr: bool) -> list[Page]:
    """Render each PDF page to text + Markdown with Docling (real converter).

    Converter configuration (all three matter — see milestone-2 notes):
      * backend = ``PyPdfiumDocumentBackend`` — the default DoclingParse backend
        clips right-edge words (e.g. "PROCESS"→"PROCES", "45001:2018"→"45001:201");
        pypdfium2 extracts the full text.
      * ``TableFormerMode.ACCURATE`` — recovers the process-gas spec tables.
      * ``do_cell_matching = False`` — with pypdfium2, cell-matching merges a
        table's label column into the value cell; disabling it repopulates cells
        from the predicted grid, restoring proper columns.
    """
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
    from docling.datamodel.base_models import DocumentStream, InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_table_structure = True
    opts.do_ocr = ocr
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    opts.table_structure_options.do_cell_matching = False
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=opts, backend=PyPdfiumDocumentBackend
            )
        }
    )
    stream = DocumentStream(name="source.pdf", stream=io.BytesIO(pdf_bytes))
    doc = converter.convert(stream).document

    pages: list[Page] = []
    for i in range(1, doc.num_pages() + 1):
        pages.append(
            Page(
                page_no=i,
                text=doc.export_to_text(page_no=i),
                # keep "&" and "_" verbatim — Docling escapes them by default,
                # which mangles catalogue text (e.g. "AIR & GAS") for review.
                markdown=doc.export_to_markdown(
                    page_no=i, escape_html=False, escape_underscores=False
                ),
            )
        )
    return pages


def _density(text: str) -> int:
    return len((text or "").strip())


def _count_tables(markdown: str) -> int:
    return len(_TABLE_SEP.findall(markdown))


def pdf_to_markdown(pdf_bytes: bytes, *, render=render_pages) -> PdfResult:
    """Convert a PDF to Markdown with page markers and conditional OCR."""
    base = render(pdf_bytes, ocr=False)
    low = [p.page_no for p in base if _density(p.text) < OCR_DENSITY_THRESHOLD]

    pages = base
    if low:
        ocr_by_page = {p.page_no: p for p in render(pdf_bytes, ocr=True)}
        pages = [ocr_by_page.get(p.page_no, p) if p.page_no in low else p for p in base]

    markdown = "\n\n".join(
        f"<!-- page {p.page_no} -->\n\n{p.markdown}".rstrip() for p in pages
    )
    return PdfResult(
        markdown=markdown,
        page_count=len(base),
        pages_ocred=list(low),
        low_density_pages=list(low),
        tables=_count_tables(markdown),
    )
