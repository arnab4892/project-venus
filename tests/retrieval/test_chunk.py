"""Chunker unit tests (LLD-RET-01/02/04) — written before the embedder exists.

Pure-Python: sections are built by hand and token counting is a deterministic
word-count fake, so these never touch a DB or the 16 MB bge-m3 tokenizer.
"""

from __future__ import annotations

import pytest

from agentkit.extract.markdown import Section
from agentkit.retrieval.chunk import (
    CHUNK,
    EXCLUDED,
    REFERENCE_ONLY,
    FamilyRefs,
    build_dedup_index,
    chunk_sections,
    seed_disposition,
    split_table_by_rows,
)
from agentkit.retrieval.tokenizer import ChunkTooLargeError

# A deterministic token counter: one token per whitespace word.
WORDS = lambda s: len(s.split())  # noqa: E731


def _section(text: str, *, heading=("Products",), doc_id="doc.x", kind="pdf") -> Section:
    return Section(
        section_id=f"{doc_id}::s000",
        doc_id=doc_id,
        kind=kind,
        url=None,
        heading_path=list(heading),
        page_start=1,
        page_end=1,
        text=text,
    )


def test_family_tagging_is_mechanical():
    """family_ids = facts rows covering the section ∪ frozen family-name matches."""
    refs = FamilyRefs(
        by_section={("doc.x", "PROCESS COMPRESSORS (RECIP.)"): {"fam.process_recip"}},
        names={"fam.diaphragm": "Diaphragm Compressors"},
        division={"fam.process_recip": "industrial", "fam.diaphragm": "industrial"},
    )
    sec = _section(
        "Reciprocating process gas compressors. Also see our Diaphragm Compressors range.",
        heading=("PROCESS COMPRESSORS (RECIP.)",),
    )
    chunks = chunk_sections(
        "doc.x", [sec], count_tokens=WORDS, embed_limit=1000, family_refs=refs
    )
    assert len(chunks) == 1
    # section-cover match + name match, both mechanical:
    assert chunks[0].family_ids == ["fam.diaphragm", "fam.process_recip"]
    assert chunks[0].division == "industrial"


def test_table_split_repeats_header():
    """A table over the budget is split by rows with the header repeated in each part."""
    table = (
        "| Model | Cap |\n| --- | --- |\n"
        "| A | 1 |\n| B | 2 |\n| C | 3 |\n| D | 4 |"
    )
    sec = _section(table)
    chunks = chunk_sections(
        "doc.x", [sec], count_tokens=WORDS, embed_limit=1000, target_max=12
    )
    assert len(chunks) > 1
    for c in chunks:
        assert "| Model | Cap |" in c.content_md  # header repeated in every part
        assert c.token_count < 1000


def test_single_table_row_over_limit_raises():
    """A row that with the header already reaches the embed limit cannot be split."""
    table = "| Model | Cap |\n| --- | --- |\n| " + " ".join(["x"] * 60) + " | 1 |"
    with pytest.raises(ChunkTooLargeError):
        split_table_by_rows(
            table, budget=20, count_tokens=WORDS, locator="p1 §T", embed_limit=50
        )


def test_oversized_paragraph_raises_chunk_too_large():
    """A prose chunk at/over the embed limit raises rather than truncating silently."""
    sec = _section(" ".join(["word"] * 60), heading=())
    with pytest.raises(ChunkTooLargeError):
        chunk_sections("doc.x", [sec], count_tokens=WORDS, embed_limit=50)


def test_disposition_heuristics_seed_the_three_values():
    stub = _section("Download PDF", heading=("Downloads",))
    assert seed_disposition("doc.stub", "sha1", "html", [stub]).disposition == EXCLUDED

    links = "\n\n".join(f"[Product {i}](/p/{i})" for i in range(12))
    hub = _section(links, heading=("Catalogue",))
    assert seed_disposition("doc.hub", "sha2", "html", [hub]).disposition == REFERENCE_ONLY

    prose = _section(" ".join(["engineering"] * 80), heading=("About",))
    assert seed_disposition("doc.body", "sha3", "pdf", [prose]).disposition == CHUNK


def test_corpus_dedup_keeps_canonical_pdf_copy():
    shared = " ".join(["shared"] * 20)  # ≥ _DEDUP_MIN_WORDS
    pdf = {"doc.pdf": [_section(shared, kind="pdf")]}
    html = {"doc.html": [_section(shared, kind="html", doc_id="doc.html")]}
    canonical, report = build_dedup_index(
        {**pdf, **html}, {"doc.pdf": "pdf", "doc.html": "html"}
    )
    assert len(report) == 1
    assert report[0]["canonical"] == "doc.pdf"  # PDF wins
    assert report[0]["dropped_from"] == ["doc.html"]

    # the non-canonical doc drops the shared paragraph at chunk time
    def keep(norm_para: str) -> bool:
        return canonical.get(norm_para, "doc.html") == "doc.html"

    chunks = chunk_sections(
        "doc.html",
        html["doc.html"],
        count_tokens=WORDS,
        embed_limit=1000,
        keep_paragraph=keep,
    )
    assert chunks == []  # its only paragraph was a duplicate owned by the PDF
