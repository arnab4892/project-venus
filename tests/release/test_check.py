"""LLD-REL-03: integrity check over the approved+edited surviving set."""

from __future__ import annotations

from agentkit.extract.staging_write import write_rows
from agentkit.release.candidate import create_rc
from agentkit.release.check import check_rc

# fam.process_recip in families.yaml is sourced from doc.jyotech_catalog_process, so
# surviving that document satisfies both provenance and family source-doc closure.
_SRC_DOC = "doc.jyotech_catalog_process"
_DOCS = [{"doc_id": _SRC_DOC, "kind": "pdf", "url": "u", "sha256": "s", "page_count": 1,
          "review_status": "approved"}]


def _prov(status="approved"):
    return dict(source_doc_id=_SRC_DOC, source_locator="§Process",
               section_id="s1", confidence=0.9, review_status=status)


def _cap(cap_id="cap.1", *, unit="Nm3/hr", needs_family=False, status="approved",
         conflict=None, family="fam.process_recip"):
    return ("capability_row", {
        "cap_id": cap_id, "family_id": family, "comp_type": "reciprocating",
        "capacity_max": 20000, "capacity_unit": unit, "needs_family": needs_family,
        "conflict_group": conflict, "evidence": {"capacity": "Up to 20000 Nm3/hr"}, **_prov(status),
    })


def _gas(cap_id, gas, status="approved"):
    return ("capability_gas", {
        "cap_id": cap_id, "gas": gas, "evidence": {"gas": gas}, "section_id": "s1",
        "confidence": 0.9, "review_status": status, "needs_family": False, "conflict_group": None,
    })


def _office(office_id="off.1", city="NOIDA", status="approved", locator="§Contact"):
    row = {
        "office_id": office_id, "name": "HQ", "city": city, "region": None,
        "address": "a", "phone": "p", "email": "e", "serves_divisions": [],
        "needs_family": False, "conflict_group": None, "evidence": {},
        "source_doc_id": _SRC_DOC, "source_locator": locator,
        "section_id": "s1", "confidence": 0.9, "review_status": status,
    }
    return ("office", row)


def _run(seeded_conn, content):
    rc = create_rc(seeded_conn, "jyotech")
    write_rows(seeded_conn, rc_id=rc, document_rows=_DOCS, content_rows=content)
    return check_rc(seeded_conn, rc, client="jyotech")


def test_clean_set_passes(seeded_conn) -> None:
    r = _run(seeded_conn, [_cap(), _gas("cap.1", "H2"), _office()])
    assert r.ok, r.hard_failures
    assert any("NOIDA" in m and "North" in m for m in r.region_mapping)


def test_numeric_without_unit_is_hard_failure(seeded_conn) -> None:
    r = _run(seeded_conn, [_cap(unit=None), _office()])
    assert not r.ok
    assert any("without a unit" in m for m in r.hard_failures)


def test_needs_family_survivor_is_hard_failure(seeded_conn) -> None:
    r = _run(seeded_conn, [_cap(needs_family=True), _office()])
    assert not r.ok
    assert any("needs_family" in m for m in r.hard_failures)


def test_missing_provenance_is_hard_failure(seeded_conn) -> None:
    r = _run(seeded_conn, [_cap(), _office(locator=None)])
    assert not r.ok
    assert any("source_doc_id/source_locator" in m for m in r.hard_failures)


def test_gas_without_surviving_parent_is_hard_failure(seeded_conn) -> None:
    # parent rejected → not in surviving set → its gas is an orphan
    r = _run(seeded_conn, [_cap(status="rejected"), _gas("cap.1", "H2"), _office()])
    assert not r.ok
    assert any("parent capability_row not in surviving set" in m for m in r.hard_failures)


def test_office_city_must_resolve(seeded_conn) -> None:
    r = _run(seeded_conn, [_cap(), _gas("cap.1", "H2"), _office(city="Atlantis")])
    assert not r.ok
    assert any("does not resolve via region_state" in m for m in r.hard_failures)


def test_surviving_conflict_group_is_warning_not_failure(seeded_conn) -> None:
    content = [
        _cap("cap.1", conflict="cg.fam.process_recip.capacity"),
        _cap("cap.2", conflict="cg.fam.process_recip.capacity"),
        _office(),
    ]
    r = _run(seeded_conn, content)
    assert r.ok, r.hard_failures
    assert any("conflict_group" in m for m in r.warnings)
