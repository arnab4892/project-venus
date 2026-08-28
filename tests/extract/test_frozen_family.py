"""LLD-EXT-03/05: frozen-family enforcement + unresolved-conflict marking. No DB.

An extractor may assign only ids present in the approved ``families.yaml``.
Anything unassignable is flagged ``needs_family`` — never given an invented id.
Two same-family rows with different published capacity are linked by a shared
``conflict_group`` (the 20000 vs 25000 process-gas case) and left unresolved.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentkit.extract.registry import load_client_schemas

schemas = load_client_schemas("jyotech")


@dataclass
class _Section:
    section_id: str
    doc_id: str
    locator: str


_SEC = _Section("doc.process::s003", "doc.process", "p4 §Process")
_FROZEN = {"fam.process_recip", "fam.oxygen_recip"}


def _cap_item(family_id: str, capacity: str) -> dict:
    return {
        "rows": [
            {
                "family": {"value": family_id, "evidence": "Process Gas Compressor"},
                "comp_type": {"value": "reciprocating", "evidence": "Reciprocating"},
                "capacity": {"value": capacity, "evidence": capacity},
                "gases": [{"value": "hydrogen", "evidence": "hydrogen"}],
            }
        ]
    }


def test_known_family_id_is_assigned() -> None:
    rows = schemas.build_rows(
        _SEC, "capability_spec", _cap_item("fam.process_recip", "up to 20000 Nm3/hr"),
        family_ids=_FROZEN, confidence=0.9,
    )
    cap = next(r for t, r in rows if t == "capability_row")
    assert cap["family_id"] == "fam.process_recip"
    assert cap["needs_family"] is False
    assert cap["capacity_max"] == 20000


def test_unknown_family_id_is_flagged_never_invented() -> None:
    rows = schemas.build_rows(
        _SEC, "capability_spec", _cap_item("fam.hydrogen_made_up", "up to 20000 Nm3/hr"),
        family_ids=_FROZEN, confidence=0.9,
    )
    cap = next(r for t, r in rows if t == "capability_row")
    assert cap["family_id"] is None          # not invented
    assert cap["needs_family"] is True


def test_gases_become_capability_gas_rows() -> None:
    rows = schemas.build_rows(
        _SEC, "capability_spec", _cap_item("fam.process_recip", "up to 20000 Nm3/hr"),
        family_ids=_FROZEN, confidence=0.9,
    )
    gas_rows = [r for t, r in rows if t == "capability_gas"]
    assert len(gas_rows) == 1
    assert gas_rows[0]["gas"] == "hydrogen"
    assert gas_rows[0]["cap_id"] == next(r for t, r in rows if t == "capability_row")["cap_id"]


def test_mark_conflicts_links_same_family_capacity() -> None:
    web = schemas.build_rows(
        _Section("doc.pgc::s001", "doc.pgc", "§Process"),
        "capability_spec", _cap_item("fam.process_recip", "up to 20000 Nm3/hr"),
        family_ids=_FROZEN, confidence=0.9,
    )
    pdf = schemas.build_rows(
        _Section("doc.process::s004", "doc.process", "p4 §Process"),
        "capability_spec", _cap_item("fam.process_recip", "up to 25000 Nm3/hr"),
        family_ids=_FROZEN, confidence=0.9,
    )
    caps = [r for t, r in web + pdf if t == "capability_row"]
    schemas.mark_conflicts(caps)
    groups = {r["conflict_group"] for r in caps}
    assert groups == {"cg.fam.process_recip.capacity"}  # both linked, none resolved
    assert {r["capacity_max"] for r in caps} == {20000, 25000}  # both kept verbatim
