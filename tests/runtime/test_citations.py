"""Citations carry locator + url per source type (PRD-F-008 / LLD-RT-05).

The grounding gate derives ``ops.citation`` rows from the cited tool results. This pins the
mapping: a document chunk cites with **locator + url**, a capability row cites by cap id, and a
company fact cites with its locator — the material a reply renders as "[page/§] (url)". Pure
Python — exercises ``ground_answer`` / ``_citations_from_record`` directly.
"""

from __future__ import annotations

from agentkit.runtime.grounding import ground_answer, used_documents
from agentkit.runtime.ops import ToolCallRecord


def _search_record() -> ToolCallRecord:
    return ToolCallRecord(
        tr_id="tr1",
        tool="search_documents",
        args={"query": "hydrogen process gas compressor"},
        result={
            "chunks": [
                {
                    "chunk_id": "ch.process.0003",
                    "doc_id": "doc.process",
                    "locator": "PROCESS.pdf §Process Compressors",
                    "url": "https://www.jyotech.com/pdf/PROCESS.pdf",
                    "content_md": "Reciprocating process gas compressor …",
                    "family_ids": ["fam.process_recip"],
                }
            ]
        },
        rows_returned=1,
        latency_ms=1,
    )


def _fact_record() -> ToolCallRecord:
    return ToolCallRecord(
        tr_id="tr2",
        tool="get_company_fact",
        args={"kind": "certification"},
        result={
            "facts": [
                {"fact_id": "cf.001", "value": "ISO 9001:2015", "source_locator": "§Certifications"}
            ]
        },
        rows_returned=1,
        latency_ms=1,
    )


def test_chunk_citation_has_locator_and_url():
    gr = ground_answer(
        "Our process gas compressors cover that duty [tr1].",
        ["tr1"],
        [_search_record()],
        ["hydrogen duty"],
    )
    assert gr.ok
    chunk_cites = [c for c in gr.citations if c.kind == "chunk"]
    assert len(chunk_cites) == 1
    c = chunk_cites[0]
    assert c.ref_id == "ch.process.0003"
    assert c.locator == "PROCESS.pdf §Process Compressors"
    assert c.url == "https://www.jyotech.com/pdf/PROCESS.pdf"


def test_company_fact_citation_has_locator():
    gr = ground_answer(
        "Jyotech holds ISO 9001:2015 [tr2].",
        ["tr2"],
        [_fact_record()],
        ["ISO?"],
    )
    assert gr.ok
    fact_cites = [c for c in gr.citations if c.kind == "fact"]
    assert fact_cites and fact_cites[0].ref_id == "cf.001"
    assert fact_cites[0].locator == "§Certifications"


def test_only_cited_results_become_citations():
    # cite only tr1 though two records are present → tr2's fact is NOT cited. The cited chunk
    # also yields its parent document (honest parent-derivation), but the uncited fact never
    # leaks in.
    gr = ground_answer(
        "Process gas range covers it [tr1].",
        ["tr1"],
        [_search_record(), _fact_record()],
        ["hydrogen duty"],
    )
    kinds = {c.kind for c in gr.citations}
    assert kinds == {"chunk", "document"}  # chunk + its parent doc, never the uncited fact
    assert "fact" not in kinds
    doc_cite = [c for c in gr.citations if c.kind == "document"][0]
    assert doc_cite.ref_id == "doc.process"


# --- used_documents: distinct docs the answer referenced (cards + multi-doc citations) --------

_PROCESS_CHUNK = {
    "chunk_id": "ch.process.0", "doc_id": "doc.process",
    "locator": "p1 §PROCESS", "url": "https://x/PROCESS.pdf",
    "content_md": "process compressors reciprocating industrial catalogue",
}
_FS_CHUNK = {
    "chunk_id": "ch.fs.0", "doc_id": "doc.fs",
    "locator": "p1 §F&S", "url": "https://x/F&S.pdf",
    "content_md": "fire rescue diving equipment catalogue breathing",
}


def _doc_ids(reps):
    return [c["doc_id"] for c in reps]


def test_used_documents_returns_each_referenced_doc_in_order():
    chunks = [_PROCESS_CHUNK, _FS_CHUNK]
    answer = "We publish our process compressors catalogue and our fire rescue diving catalogue."
    assert _doc_ids(used_documents(chunks, answer)) == ["doc.process", "doc.fs"]


def test_used_documents_dedupes_and_caps():
    # two chunks from the same doc collapse to one representative; cap bounds the list
    chunks = [_PROCESS_CHUNK, dict(_PROCESS_CHUNK, chunk_id="ch.process.1"), _FS_CHUNK]
    assert _doc_ids(used_documents(chunks, "process rescue diving catalogue")) == [
        "doc.process", "doc.fs",
    ]
    assert _doc_ids(
        used_documents(chunks, "process rescue diving catalogue", cap=1)
    ) == ["doc.process"]


def test_used_documents_single_reference_yields_one():
    chunks = [_PROCESS_CHUNK, _FS_CHUNK]
    assert _doc_ids(used_documents(chunks, "our fire rescue diving catalogue")) == ["doc.fs"]


def test_used_documents_no_overlap_falls_back_to_one():
    chunks = [_PROCESS_CHUNK, _FS_CHUNK]
    reps = used_documents(chunks, "completely unrelated prose about weather")
    assert len(reps) == 1  # never zero the source


def test_both_referenced_documents_are_cited():
    rec = ToolCallRecord(
        tr_id="tr1", tool="search_documents",
        args={"query": "catalogues"},
        result={"chunks": [_PROCESS_CHUNK, _FS_CHUNK]},
        rows_returned=2, latency_ms=1,
    )
    gr = ground_answer(
        "Here are our process compressors catalogue and fire rescue diving catalogue [tr1].",
        ["tr1"], [rec], ["what catalogues do you have"],
    )
    assert gr.ok
    doc_ids = {c.ref_id for c in gr.citations if c.kind == "document"}
    assert {"doc.process", "doc.fs"} <= doc_ids
