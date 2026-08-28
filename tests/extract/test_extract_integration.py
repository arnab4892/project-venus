"""Integration: canned sections → classify → typed extract → staging (LLM mocked).

Exercises the whole pass-2 pipeline against the real Jyotech schemas: the evidence
gate, frozen-family enforcement (a needs_family row), the 20000-vs-25000
conflict_group pair, gases fanned to capability_gas, and the staging upsert.
Uses the rolled-back seeded_conn — nothing persists.
"""

from __future__ import annotations

import json

from sqlalchemy import text

from agentkit.extract.classify import classify_sections
from agentkit.extract.extractors import extract_sections
from agentkit.extract.markdown import Section
from agentkit.extract.registry import load_client_schemas
from agentkit.extract.staging_write import write_rows
from agentkit.release.candidate import create_rc

schemas = load_client_schemas("jyotech")
_FROZEN = {"fam.process_recip", "fam.oxygen_recip"}

_SECTIONS = [
    Section("doc.pgc::s001", "doc.pgc", "html", "http://x/pgc.html", ["Process Compressors"],
            None, None,
            "PROCESS COMPRESSORS RECIP. Reciprocating, Non-Lubricated, Water Cooled. "
            "Capacity Up to 20000 Nm3/hr. Discharge up to 1000 Barg. hydrogen, BOG."),
    Section("doc.process::s002", "doc.process", "pdf", "http://x/PROCESS.pdf",
            ["Process Compressors"], 4, 4,
            "PROCESS COMPRESSORS RECIP. Reciprocating, Non-Lubricated, Water Cooled. "
            "Capacity Up to 25000 Nm3/hr. Discharge up to 1000 Barg. hydrogen, BOG."),
    Section("doc.process::s003", "doc.process", "pdf", "http://x/PROCESS.pdf",
            ["Novel Section"], 9, 9,
            "Novel Compressor Xyz for special duty, capacity Up to 5000 Nm3/hr."),
    Section("doc.about::s001", "doc.about", "html", "http://x/about.html", ["About"],
            None, None, "ISO 9001:2015 certified since 1991."),
    Section("doc.career::s001", "doc.career", "html", "http://x/career.php", ["Careers"],
            None, None, "Join our team, apply now."),
]

_DOCS = [
    {"doc_id": "doc.pgc", "kind": "html", "url": "http://x/pgc.html", "sha256": "a", "page_count": 1},
    {"doc_id": "doc.process", "kind": "pdf", "url": "http://x/PROCESS.pdf", "sha256": "b",
     "page_count": 18},
    {"doc_id": "doc.about", "kind": "html", "url": "http://x/about.html", "sha256": "c",
     "page_count": 1},
]


def _fake(messages, schema, name):
    text_ = messages[-1]["content"]
    if name == "section_classification":
        if "Nm3/hr" in text_:
            t = "capability_spec"
        elif "ISO 9001" in text_:
            t = "company_fact"
        else:
            t = "other"
        return json.dumps({"type": t, "confidence": 0.95})
    if name == "extract_capability_spec":
        fam = "fam.process_recip" if "PROCESS COMPRESSORS" in text_ else "fam.novel_xyz"
        fam_ev = "PROCESS COMPRESSORS" if "PROCESS COMPRESSORS" in text_ else "Novel Compressor Xyz"
        cap = "Up to 25000 Nm3/hr" if "25000" in text_ else (
            "Up to 20000 Nm3/hr" if "20000" in text_ else "Up to 5000 Nm3/hr")
        row = {
            "family": {"value": fam, "evidence": fam_ev},
            "comp_type": {"value": "reciprocating", "evidence": "Reciprocating, Non-Lubricated"}
            if "Reciprocating" in text_ else None,
            "capacity": {"value": cap.lower(), "evidence": cap},
            "gases": [{"value": g, "evidence": g} for g in ("hydrogen", "BOG") if g in text_],
        }
        row = {k: v for k, v in row.items() if v is not None}
        return json.dumps({"rows": [row]})
    if name == "extract_company_fact":
        return json.dumps({"facts": [
            {"kind": {"value": "certification", "evidence": "certified"},
             "value": {"value": "ISO 9001:2015", "evidence": "ISO 9001:2015"}}
        ]})
    return json.dumps({})


def _count(conn, table, rc) -> int:
    return conn.execute(
        text(f"SELECT count(*) FROM staging.{table} WHERE release_candidate_id=:rc"), {"rc": rc}
    ).scalar_one()


def test_full_staged_set(seeded_conn) -> None:
    rc = create_rc(seeded_conn, "jyotech")
    classifications = classify_sections(_SECTIONS, "P", complete=_fake)
    by_id = {s.section_id: s for s in _SECTIONS}
    kept = [(by_id[c.section_id], c) for c in classifications if c.type != "other"]

    result = extract_sections(
        kept, schemas=schemas, prompt_bodies={t: "P" for t in schemas.SCHEMAS},
        family_ids=_FROZEN, family_catalog="fam.process_recip", complete=_fake,
    )
    counts = write_rows(seeded_conn, rc_id=rc, document_rows=_DOCS, content_rows=result.rows)

    # Three capability rows (two PROCESS + one novel), one company fact, gases, 3 docs.
    assert counts["capability_row"] == 3
    assert counts["company_fact"] == 1
    assert counts["document"] == 3
    assert _count(seeded_conn, "capability_row", rc) == 3
    assert _count(seeded_conn, "capability_gas", rc) == 4  # 2 gases × 2 PROCESS rows

    # The 20000/25000 conflict pair is linked, not resolved.
    conflicts = seeded_conn.execute(text(
        "SELECT capacity_max, conflict_group FROM staging.capability_row "
        "WHERE release_candidate_id=:rc AND conflict_group IS NOT NULL ORDER BY capacity_max"),
        {"rc": rc}).mappings().all()
    assert [c["capacity_max"] for c in conflicts] == [20000, 25000]
    assert {c["conflict_group"] for c in conflicts} == {"cg.fam.process_recip.capacity"}

    # The novel family could not be placed → flagged, id left null (never invented).
    novel = seeded_conn.execute(text(
        "SELECT family_id, needs_family FROM staging.capability_row "
        "WHERE release_candidate_id=:rc AND capacity_max=5000"), {"rc": rc}).mappings().one()
    assert novel["needs_family"] is True
    assert novel["family_id"] is None

    # Everything staged is pending, awaiting review.
    pending = seeded_conn.execute(text(
        "SELECT count(*) FROM staging.capability_row "
        "WHERE release_candidate_id=:rc AND review_status='pending'"), {"rc": rc}).scalar_one()
    assert pending == 3
