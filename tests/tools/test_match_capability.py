"""LLD-TOOL-01 — ``match_capability`` over the ``facts.active_*`` views.

Cases trace ``docs/design/data-model.md`` §4 (the hydrogen conversation) and
the edge behaviour the LLD calls out: out-of-range, wrong-gas, near-edge, and
SCMD↔Nm3/hr conversion (LLD-EXT-09). A final case proves the ``active_*`` views
follow ``release.is_active`` so the tool serves exactly the live release.

Written before ``agentkit.tools.match_capability`` exists (test-first).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from agentkit.tools.match_capability import match_capability


def _cap_ids(result: dict) -> list[str]:
    return [m["cap_id"] for m in result["matches"]]


def test_hydrogen_in_range(seeded_conn):
    """§4 turn 1: hydrogen 3000 Nm³/hr @ 350 barg, oil-free → cap.002 only, not near edge."""
    result = match_capability(
        seeded_conn,
        gas="hydrogen",
        capacity=3000,
        capacity_unit="Nm3/hr",
        discharge_p=350,
        lubricated=False,
    )
    assert _cap_ids(result) == ["cap.002"]
    match = result["matches"][0]
    assert match["family_id"] == "fam.process_recip"
    assert match["headroom"]["capacity"] == pytest.approx(0.85)
    assert match["headroom"]["pressure"] == pytest.approx(0.65)
    assert result["near_edge"] is False


def test_pressure_out_of_range(seeded_conn):
    """1200 barg exceeds cap.002's 1000 barg ceiling → no match."""
    result = match_capability(
        seeded_conn,
        gas="hydrogen",
        capacity=3000,
        capacity_unit="Nm3/hr",
        discharge_p=1200,
        lubricated=False,
    )
    assert result["matches"] == []
    assert result["near_edge"] is False


def test_wrong_gas_pressure_ceiling(seeded_conn):
    """Oxygen is only served by cap.001, which tops out at 50 barg → 350 barg has no match."""
    result = match_capability(
        seeded_conn,
        gas="oxygen",
        capacity=3000,
        capacity_unit="Nm3/hr",
        discharge_p=350,
        lubricated=False,
    )
    assert result["matches"] == []


def test_near_edge_capacity(seeded_conn):
    """19,500 Nm³/hr is 97.5% of cap.002's 20,000 ceiling → match, near_edge true."""
    result = match_capability(
        seeded_conn,
        gas="hydrogen",
        capacity=19500,
        capacity_unit="Nm3/hr",
        discharge_p=350,
        lubricated=False,
    )
    assert _cap_ids(result) == ["cap.002"]
    assert result["matches"][0]["headroom"]["capacity"] == pytest.approx(0.025)
    assert result["near_edge"] is True


def test_scmd_unit_conversion(seeded_conn):
    """Natural gas 1000 Nm³/hr (=24,000 SCMD) lubricated @ 100 barg → cap.003 (SCMD row) only.

    cap.002 also serves natural gas but is non-lubricated, so ``lubricated=True``
    isolates the SCMD-stored cap.003 and forces the Nm3/hr→SCMD conversion
    (1 Nm3/hr = 24 SCMD; 24,000 ∈ [10,000, 100,000]).
    """
    result = match_capability(
        seeded_conn,
        gas="natural gas",
        capacity=1000,
        capacity_unit="Nm3/hr",
        discharge_p=100,
        lubricated=True,
    )
    assert _cap_ids(result) == ["cap.003"]
    match = result["matches"][0]
    assert match["family_id"] == "fam.natgas_hbo"
    # 24,000 / 100,000 SCMD → capacity headroom 0.76
    assert match["headroom"]["capacity"] == pytest.approx(0.76)


def test_active_views_follow_is_active(seeded_conn):
    """Copy r2026.08.1 as an inactive r-test with a different pressure ceiling; flipping
    ``is_active`` flips which release's values the ``active_*`` views (and the tool) serve."""
    conn = seeded_conn

    # --- clone the fam.process_recip lineage into an inactive r-test (disch_p_max 999) ---
    conn.execute(
        text(
            "INSERT INTO facts.release (release_id, built_at, source_manifest, is_active, notes) "
            "VALUES ('r-test', now(), '{}'::jsonb, false, 'flip test')"
        )
    )
    conn.execute(
        text(
            "INSERT INTO facts.document "
            "(doc_id, release_id, kind, title, url, division, sha256, page_count) "
            "SELECT doc_id, 'r-test', kind, title, url, division, sha256, page_count "
            "FROM facts.document WHERE release_id='r2026.08.1' AND doc_id='doc.process'"
        )
    )
    conn.execute(
        text(
            "INSERT INTO facts.product_family "
            "(family_id, release_id, division, category, name, summary, applications, standards, "
            " source_doc_id, source_locator) "
            "SELECT family_id, 'r-test', division, category, name, summary, applications, standards, "
            " source_doc_id, source_locator "
            "FROM facts.product_family WHERE release_id='r2026.08.1' AND family_id='fam.process_recip'"
        )
    )
    conn.execute(
        text(
            "INSERT INTO facts.capability_row "
            "(cap_id, release_id, family_id, comp_type, lubricated, cooling, capacity_min, "
            " capacity_max, capacity_unit, discharge_p_min, discharge_p_max, pressure_unit, "
            " driver, standards, source_doc_id, source_locator) "
            "SELECT cap_id, 'r-test', family_id, comp_type, lubricated, cooling, capacity_min, "
            " capacity_max, capacity_unit, discharge_p_min, 999, pressure_unit, "
            " driver, standards, source_doc_id, source_locator "
            "FROM facts.capability_row WHERE release_id='r2026.08.1' AND cap_id='cap.002'"
        )
    )
    conn.execute(
        text(
            "INSERT INTO facts.capability_gas (cap_id, release_id, gas) "
            "VALUES ('cap.002', 'r-test', 'hydrogen')"
        )
    )

    # active release is still r2026.08.1 → pressure ceiling 1000 → headroom 0.65
    before = match_capability(
        conn, gas="hydrogen", capacity=3000, capacity_unit="Nm3/hr",
        discharge_p=350, lubricated=False,
    )
    assert _cap_ids(before) == ["cap.002"]
    assert before["matches"][0]["headroom"]["pressure"] == pytest.approx(0.65)

    # flip active release (two statements to respect the one-active partial unique index)
    conn.execute(text("UPDATE facts.release SET is_active=false"))
    conn.execute(text("UPDATE facts.release SET is_active=true WHERE release_id='r-test'"))

    after = match_capability(
        conn, gas="hydrogen", capacity=3000, capacity_unit="Nm3/hr",
        discharge_p=350, lubricated=False,
    )
    assert _cap_ids(after) == ["cap.002"]
    # now served from r-test → pressure ceiling 999 → different headroom
    assert after["matches"][0]["headroom"]["pressure"] == pytest.approx(1 - 350 / 999)


def test_unknown_capacity_unit_is_non_comparable_not_a_crash(seeded_conn):
    """A row stored in a unit outside the canonical vocabulary (e.g. Kg/hr) must not
    crash the tool: capacity is non-comparable (headroom None), pressure still applies."""
    conn = seeded_conn
    conn.execute(text(
        "INSERT INTO facts.capability_row "
        "(cap_id, release_id, family_id, comp_type, capacity_max, capacity_unit, "
        " discharge_p_max, pressure_unit, source_doc_id, source_locator) "
        "SELECT 'cap.kghr', 'r2026.08.1', 'fam.process_recip', 'diaphragm', 500, 'Kg/hr', "
        " 850, 'barg', source_doc_id, source_locator "
        "FROM facts.capability_row WHERE release_id='r2026.08.1' AND cap_id='cap.002'"
    ))
    conn.execute(text(
        "INSERT INTO facts.capability_gas (cap_id, release_id, gas) "
        "VALUES ('cap.kghr', 'r2026.08.1', 'hydrogen')"
    ))
    result = match_capability(
        conn, gas="hydrogen", capacity=3000, capacity_unit="Nm3/hr", discharge_p=350,
    )
    match = next(m for m in result["matches"] if m["cap_id"] == "cap.kghr")
    assert match["headroom"]["capacity"] is None       # non-comparable, never guessed
    assert match["headroom"]["pressure"] == pytest.approx(1 - 350 / 850)


def test_one_active_release_enforced(seeded_conn):
    """The partial unique index on release(is_active) forbids a second active release."""
    from sqlalchemy.exc import IntegrityError

    conn = seeded_conn
    sp = conn.begin_nested()
    with pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO facts.release (release_id, built_at, source_manifest, is_active, notes) "
                "VALUES ('r-second', now(), '{}'::jsonb, true, 'should fail')"
            )
        )
    sp.rollback()
