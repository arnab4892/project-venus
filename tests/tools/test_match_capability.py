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


def _noncmp_ids(result: dict) -> list[str]:
    return [c["cap_id"] for c in result["non_comparable_candidates"]]


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
    # the match carries its own published limits so the agent answers from the row, not chunks
    assert match["capacity_max"] == pytest.approx(20000)
    assert match["capacity_unit"] == "Nm3/hr"
    assert match["discharge_p_max"] == pytest.approx(1000)
    assert match["near_edge"] is False
    assert result["any_near_edge"] is False


def _add_cap(conn, cap_id, *, cap_min, cap_max, cap_unit, p_min, p_max, p_unit, lubricated="NULL"):
    """Insert a hydrogen-serving capability row under fam.process_recip (source copied from cap.002)."""
    conn.execute(text(
        "INSERT INTO facts.capability_row "
        "(cap_id, release_id, family_id, comp_type, lubricated, capacity_min, capacity_max, "
        " capacity_unit, discharge_p_min, discharge_p_max, pressure_unit, source_doc_id, source_locator) "
        f"SELECT :cid, 'r2026.08.1', 'fam.process_recip', 'recip', {lubricated}, :cmin, :cmax, "
        " :cunit, :pmin, :pmax, :punit, source_doc_id, source_locator "
        "FROM facts.capability_row WHERE release_id='r2026.08.1' AND cap_id='cap.002'"
    ), {"cid": cap_id, "cmin": cap_min, "cmax": cap_max, "cunit": cap_unit,
        "pmin": p_min, "pmax": p_max, "punit": p_unit})
    conn.execute(text(
        "INSERT INTO facts.capability_gas (cap_id, release_id, gas) VALUES (:cid,'r2026.08.1','hydrogen')"
    ), {"cid": cap_id})


def test_hydrogen_3000_regression_matches_vs_non_comparable(seeded_conn):
    """Regression for the live hydrogen mismatch (ops turn ab357000).

    With exactly {H2, 3000 Nm³/hr, 350 barg, oil-free} against a realistic topology — a large
    process row (Nm³/hr), a small 700-row (Nm³/hr), a Kg/hr row, and a null-capacity fuelling
    row — the ONLY match is the process family; the 700-row is excluded (comparable fail), and
    the Kg/hr + null-capacity rows are non-comparable candidates (never matches).
    """
    conn = seeded_conn
    # cap.002 = the big process row (20000 Nm³/hr / 1000 barg, hydrogen) from the demo seed.
    _add_cap(conn, "cap.h2_700", cap_min=None, cap_max=700, cap_unit="Nm3/hr",
             p_min=None, p_max=850, p_unit="barg")
    _add_cap(conn, "cap.h2_kghr", cap_min=35, cap_max=65, cap_unit="Kg/hr",
             p_min=250, p_max=850, p_unit="barg")
    _add_cap(conn, "cap.h2_fuel", cap_min=None, cap_max=None, cap_unit=None,
             p_min=350, p_max=350, p_unit="barg")

    result = match_capability(
        conn, gas="hydrogen", capacity=3000, capacity_unit="Nm3/hr",
        discharge_p=350, lubricated=False,
    )

    # top match is the process family (the only row whose comparable envelope contains the duty)
    assert _cap_ids(result)[0] == "cap.002"
    top = result["matches"][0]
    assert top["family_id"] == "fam.process_recip"
    assert top["near_edge"] is False
    assert top["headroom"]["capacity"] == pytest.approx(0.85)
    assert top["headroom"]["pressure"] == pytest.approx(0.65)

    # the 700-row failed the (comparable) capacity filter → excluded from BOTH lists
    assert "cap.h2_700" not in _cap_ids(result)
    assert "cap.h2_700" not in _noncmp_ids(result)

    # kg/hr + null-capacity rows are non-comparable candidates, never matches
    assert "cap.h2_kghr" not in _cap_ids(result)
    assert "cap.h2_fuel" not in _cap_ids(result)
    assert {"cap.h2_kghr", "cap.h2_fuel"} <= set(_noncmp_ids(result))
    assert result["any_near_edge"] is False


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
    assert result["any_near_edge"] is False


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
    assert result["matches"][0]["near_edge"] is True
    assert result["any_near_edge"] is True


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


def test_converted_capacity_exposed_on_cross_unit_match(seeded_conn):
    """Additive (LLD-TOOL-01, Fix 3): a family reached by unit conversion carries the tool-computed
    converted duty, so the compose reconciles units from a sourced figure (no prose arithmetic).

    Natural gas 1000 Nm³/hr against the SCMD-published cap.003 → 1000 × 24 = 24,000 SCMD.
    """
    result = match_capability(
        seeded_conn,
        gas="natural gas",
        capacity=1000,
        capacity_unit="Nm3/hr",
        discharge_p=100,
        lubricated=True,
    )
    match = result["matches"][0]
    assert match["family_id"] == "fam.natgas_hbo"
    assert match["same_unit"] is False
    cv = match["converted_capacity"]
    assert cv["value"] == pytest.approx(24000)
    assert cv["unit"] == "SCMD"
    assert cv["from"] == "Nm3/hr"


def test_no_converted_capacity_on_same_unit_match(seeded_conn):
    """A same-unit match omits converted_capacity — no conversion happened, nothing to reconcile."""
    result = match_capability(
        seeded_conn,
        gas="hydrogen",
        capacity=3000,
        capacity_unit="Nm3/hr",
        discharge_p=350,
        lubricated=False,
    )
    match = result["matches"][0]
    assert match["same_unit"] is True
    assert "converted_capacity" not in match


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


def test_kghr_row_is_a_non_comparable_candidate_not_a_match(seeded_conn):
    """A row stored in a unit outside the query basis (e.g. Kg/hr vs an Nm³/hr query) is a
    non-comparable CANDIDATE with a reason — never a match, never guessed, never a crash."""
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
    assert "cap.kghr" not in _cap_ids(result)          # not presented as a match
    candidate = next(c for c in result["non_comparable_candidates"] if c["cap_id"] == "cap.kghr")
    assert "capacity" in candidate["reason"].lower()   # reason names the unevaluable dimension


def test_gas_alias_map_bridges_hydrogen_to_H2(seeded_conn):
    """A row stored verbatim as 'H2' (like the live catalogue) is reached by a 'hydrogen'
    query ONLY when the per-client gas alias map is applied; facts stay verbatim."""
    conn = seeded_conn
    conn.execute(text(
        "INSERT INTO facts.capability_row (cap_id, release_id, family_id, comp_type, lubricated,"
        " capacity_max, capacity_unit, discharge_p_max, pressure_unit, source_doc_id, source_locator) "
        "SELECT 'cap.h2sym', 'r2026.08.1', 'fam.process_recip', 'recip', false, 20000, 'Nm3/hr',"
        " 1000, 'barg', source_doc_id, source_locator "
        "FROM facts.capability_row WHERE release_id='r2026.08.1' AND cap_id='cap.002'"
    ))
    conn.execute(text(
        "INSERT INTO facts.capability_gas (cap_id, release_id, gas) VALUES ('cap.h2sym','r2026.08.1','H2')"
    ))
    args = dict(gas="hydrogen", capacity=3000, capacity_unit="Nm3/hr", discharge_p=350, lubricated=False)

    plain = match_capability(conn, **args)  # no alias map
    assert "cap.h2sym" not in _cap_ids(plain)

    aliased = match_capability(conn, **args, gas_aliases={"hydrogen": "H2", "h2": "H2"})
    assert "cap.h2sym" in _cap_ids(aliased)


def test_null_lubricated_not_excluded_by_oil_free_filter(seeded_conn):
    """null lubricated = 'unspecified / both offered': kept under an oil-free filter, flagged."""
    conn = seeded_conn
    conn.execute(text(
        "INSERT INTO facts.capability_row (cap_id, release_id, family_id, comp_type, lubricated,"
        " capacity_max, capacity_unit, discharge_p_max, pressure_unit, source_doc_id, source_locator) "
        "SELECT 'cap.nolub', 'r2026.08.1', 'fam.process_recip', 'recip', NULL, 20000, 'Nm3/hr',"
        " 1000, 'barg', source_doc_id, source_locator "
        "FROM facts.capability_row WHERE release_id='r2026.08.1' AND cap_id='cap.002'"
    ))
    conn.execute(text(
        "INSERT INTO facts.capability_gas (cap_id, release_id, gas) VALUES ('cap.nolub','r2026.08.1','hydrogen')"
    ))
    result = match_capability(
        conn, gas="hydrogen", capacity=3000, capacity_unit="Nm3/hr", discharge_p=350, lubricated=False
    )
    match = next(m for m in result["matches"] if m["cap_id"] == "cap.nolub")
    assert match["lubricated"] is None
    assert match["lubricated_unspecified"] is True


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
