"""LLD-REL-05: promote gate, facts.* FK closure, and active-view flips."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from agentkit.extract.staging_write import write_rows
from agentkit.release.candidate import create_rc, get_rc, set_status
from agentkit.release.promote import activate, promote

# A far-future month with no releases, so id allocation is deterministic regardless of
# any releases committed to the dev DB by a real `release promote` run.
_NOW = datetime(2030, 1, 15, tzinfo=timezone.utc)
_NEW_RELEASE = "r2030.01.1"
_SRC_DOC = "doc.jyotech_catalog_process"  # fam.process_recip's source doc in families.yaml
_DOCS = [{"doc_id": _SRC_DOC, "kind": "pdf", "url": "u", "sha256": "s",
          "page_count": 1, "review_status": "approved"}]
_PROV = dict(source_doc_id=_SRC_DOC, source_locator="§Process", section_id="s1",
             confidence=0.9, review_status="approved")


def _content() -> list[tuple[str, dict]]:
    cap = ("capability_row", {
        "cap_id": "cap.new1", "family_id": "fam.process_recip", "comp_type": "reciprocating",
        "capacity_max": 20000, "capacity_unit": "Nm3/hr", "needs_family": False,
        "conflict_group": None, "evidence": {}, **_PROV,
    })
    gas = ("capability_gas", {
        "cap_id": "cap.new1", "gas": "H2", "evidence": {"gas": "H2"}, "section_id": "s1",
        "confidence": 0.9, "review_status": "approved", "needs_family": False, "conflict_group": None,
    })
    prod = ("product", {
        "product_id": "prd.new1", "family_id": "fam.process_recip", "model_name": "X",
        "attributes": {}, "needs_family": False, "conflict_group": None, "evidence": {}, **_PROV,
    })
    fact = ("company_fact", {
        "fact_id": "cf.new1", "kind": "certification", "value": "ISO 9001", "detail": {},
        "needs_family": False, "conflict_group": None, "evidence": {}, **_PROV,
    })
    office = ("office", {
        "office_id": "off.doc_contact_s000.0", "name": "HQ", "city": "NOIDA", "region": None,
        "address": "a", "phone": "p", "email": "e", "serves_divisions": [],
        "needs_family": False, "conflict_group": None, "evidence": {}, **_PROV,
    })
    return [cap, gas, prod, fact, office]


def _imported_rc(seeded_conn) -> str:
    rc = create_rc(seeded_conn, "jyotech")
    write_rows(seeded_conn, rc_id=rc, document_rows=_DOCS, content_rows=_content())
    set_status(seeded_conn, rc, "imported")
    return rc


# --- gate --------------------------------------------------------------------

def test_promote_refuses_missing_ledger_row(seeded_conn) -> None:
    with pytest.raises(ValueError, match="no release_candidate ledger row"):
        promote(seeded_conn, "rc.jyotech.bootstrap", client="jyotech")


def test_promote_refuses_open_status(seeded_conn) -> None:
    rc = create_rc(seeded_conn, "jyotech")  # status open
    with pytest.raises(ValueError, match="not in"):
        promote(seeded_conn, rc, client="jyotech")


# --- promote + activate/rollback ---------------------------------------------

def test_promote_inserts_facts_and_views_flip(seeded_conn) -> None:
    rc = _imported_rc(seeded_conn)
    result = promote(seeded_conn, rc, client="jyotech", now=_NOW)

    assert result["release_id"] == _NEW_RELEASE      # next N in the (empty) month
    assert result["counts"]["product_family"] == 1   # only fam.process_recip, from families.yaml
    assert result["counts"]["capability_gas"] == 1
    assert result["counts"]["region_state"] == 1     # NOIDA office → Uttar Pradesh entry
    assert get_rc(seeded_conn, rc)["status"] == "promoted"

    # New release exists but is NOT active yet; the seed is still active.
    active = seeded_conn.execute(text("SELECT release_id FROM facts.active_release")).scalar_one()
    assert active == "r2026.08.1"

    def active_caps():
        return {r[0] for r in seeded_conn.execute(
            text("SELECT cap_id FROM facts.active_capability_row")).all()}

    assert "cap.new1" not in active_caps()

    # Activate → views switch to the new release.
    activate(seeded_conn, _NEW_RELEASE)
    assert seeded_conn.execute(text("SELECT release_id FROM facts.active_release")).scalar_one() == _NEW_RELEASE
    assert active_caps() == {"cap.new1"}

    # Rollback → prior release live again.
    activate(seeded_conn, "r2026.08.1")
    assert seeded_conn.execute(text("SELECT release_id FROM facts.active_release")).scalar_one() == "r2026.08.1"
    assert "cap.new1" not in active_caps()

    # Re-activate the new release.
    activate(seeded_conn, _NEW_RELEASE)
    assert active_caps() == {"cap.new1"}


def test_activate_unknown_release_raises(seeded_conn) -> None:
    with pytest.raises(ValueError, match="unknown release"):
        activate(seeded_conn, "r2099.01.9")
