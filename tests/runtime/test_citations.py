"""Citations carry locator + url per source type (PRD-F-008 / LLD-RT-05).

The grounding gate derives ``ops.citation`` rows from the cited tool results. This pins the
mapping: a document chunk cites with **locator + url**, a capability row cites by cap id, and a
company fact cites with its locator — the material a reply renders as "[page/§] (url)". Pure
Python — exercises ``ground_answer`` / ``_citations_from_record`` directly.
"""

from __future__ import annotations

from agentkit.runtime.grounding import ground_answer
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
