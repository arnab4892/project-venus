"""LLD-REL-04: diff the surviving set against the active release."""

from __future__ import annotations

from sqlalchemy import text

from agentkit.extract.staging_write import write_rows
from agentkit.release.candidate import create_rc
from agentkit.release.diff import diff_rc

_DOCS = [{"doc_id": "doc.process", "kind": "pdf", "url": "u", "sha256": "s",
          "page_count": 1, "review_status": "approved"}]
_PROV = dict(source_doc_id="doc.process", source_locator="§P", section_id="s1",
             confidence=0.9, review_status="approved")


def _cap(cap_id, capacity_max):
    return ("capability_row", {
        "cap_id": cap_id, "family_id": "fam.process_recip", "comp_type": "reciprocating",
        "capacity_max": capacity_max, "capacity_unit": "Nm3/hr", "needs_family": False,
        "conflict_group": None, "evidence": {}, **_PROV,
    })


def test_diff_reports_new_and_changed(seeded_conn) -> None:
    active = seeded_conn.execute(
        text("SELECT cap_id, capacity_max FROM facts.active_capability_row ORDER BY cap_id")
    ).mappings().all()
    assert active, "demo seed should have active capability rows"
    existing = active[0]
    changed_val = (float(existing["capacity_max"] or 0)) + 12345

    rc = create_rc(seeded_conn, "jyotech")
    write_rows(seeded_conn, rc_id=rc, document_rows=_DOCS, content_rows=[
        _cap(existing["cap_id"], changed_val),   # same id, changed numeric
        _cap("cap.brand_new", 9000),             # new id
    ])
    r = diff_rc(seeded_conn, rc)

    assert r.active_release_id == "r2026.08.1"
    assert "cap.brand_new" in r.new_caps
    assert any(c[0] == existing["cap_id"] and c[1] == "capacity_max" for c in r.changed)
    assert not r.everything_new  # there was an overlap


def test_diff_everything_new_when_no_overlap(seeded_conn) -> None:
    rc = create_rc(seeded_conn, "jyotech")
    write_rows(seeded_conn, rc_id=rc, document_rows=_DOCS,
               content_rows=[_cap("cap.only_new_1", 1), _cap("cap.only_new_2", 2)])
    r = diff_rc(seeded_conn, rc)
    assert r.new_caps == ["cap.only_new_1", "cap.only_new_2"]
    assert r.changed == []
    assert r.everything_new
    assert "everything is new" in _fmt(r)


def _fmt(r):
    from agentkit.release.diff import format_diff
    return format_diff(r)
