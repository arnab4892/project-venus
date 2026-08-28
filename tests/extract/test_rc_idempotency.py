"""LLD-EXT-10 / LLD-DB-06: re-running extraction replaces PENDING rows only.

Reviewed rows (approved/edited/rejected, arriving via a future import) must
survive a re-extraction untouched; stale pending rows must be cleared.
Uses the rolled-back seeded_conn.
"""

from __future__ import annotations

from sqlalchemy import text

from agentkit.extract.staging_write import write_rows
from agentkit.release.candidate import create_rc

_DOCS = [{"doc_id": "doc.process", "kind": "pdf", "url": "u", "sha256": "s", "page_count": 1}]


def _cap(cap_id: str, cap_max: int, status: str = "pending") -> tuple[str, dict]:
    return ("capability_row", {
        "cap_id": cap_id,
        "family_id": "fam.process_recip",
        "capacity_max": cap_max,
        "capacity_unit": "Nm3/hr",
        "needs_family": False,
        "conflict_group": None,
        "evidence": {"capacity": "up to X"},
        "source_doc_id": "doc.process",
        "source_locator": "p1",
        "section_id": "doc.process::s001",
        "confidence": 0.9,
        "review_status": status,
    })


def _count(conn, cap_id: str, rc: str) -> int:
    return conn.execute(
        text("SELECT count(*) FROM staging.capability_row WHERE cap_id=:c AND release_candidate_id=:rc"),
        {"c": cap_id, "rc": rc},
    ).scalar_one()


def _get(conn, cap_id: str, rc: str) -> dict:
    return dict(conn.execute(
        text("SELECT * FROM staging.capability_row WHERE cap_id=:c AND release_candidate_id=:rc"),
        {"c": cap_id, "rc": rc},
    ).mappings().one())


def test_rerun_replaces_pending_and_preserves_reviewed(seeded_conn) -> None:
    rc = create_rc(seeded_conn, "jyotech")

    # First extraction: two pending capability rows.
    write_rows(seeded_conn, rc_id=rc, document_rows=_DOCS,
               content_rows=[_cap("cap.a", 20000), _cap("cap.b", 30000)])
    assert _count(seeded_conn, "cap.a", rc) == 1
    assert _count(seeded_conn, "cap.b", rc) == 1

    # A reviewer approves cap.a (as import would).
    seeded_conn.execute(text(
        "UPDATE staging.capability_row SET review_status='approved' "
        "WHERE cap_id='cap.a' AND release_candidate_id=:rc"), {"rc": rc})

    # Re-extraction proposes a DIFFERENT cap.a value and drops cap.b entirely.
    write_rows(seeded_conn, rc_id=rc, document_rows=_DOCS,
               content_rows=[_cap("cap.a", 25000)])

    # cap.a stays approved with its reviewed value — not overwritten.
    a = _get(seeded_conn, "cap.a", rc)
    assert a["review_status"] == "approved"
    assert a["capacity_max"] == 20000

    # cap.b was only ever pending → replaced away (gone).
    assert _count(seeded_conn, "cap.b", rc) == 0


def test_rerun_refreshes_pending_rows(seeded_conn) -> None:
    rc = create_rc(seeded_conn, "jyotech")
    write_rows(seeded_conn, rc_id=rc, document_rows=_DOCS, content_rows=[_cap("cap.a", 20000)])
    # Same id, still pending → new run replaces it with the fresh value.
    write_rows(seeded_conn, rc_id=rc, document_rows=_DOCS, content_rows=[_cap("cap.a", 25000)])
    a = _get(seeded_conn, "cap.a", rc)
    assert a["capacity_max"] == 25000
    assert _count(seeded_conn, "cap.a", rc) == 1
