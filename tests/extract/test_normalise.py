"""LLD-EXT-09: unit canonicalisation + 'up to X' range parsing. Pure-Python."""

from __future__ import annotations

import pytest

from agentkit.extract.normalise import (
    SCMD_PER_NM3HR,
    canonical_capacity_unit,
    canonical_pressure_unit,
    convert_capacity,
    parse_capacity,
    parse_pressure,
)


@pytest.mark.parametrize(
    "raw,expected",
    [("Nm3/hr", "Nm3/hr"), ("Nm³/hr", "Nm3/hr"), ("NM3/HR", "Nm3/hr"), ("scmd", "SCMD")],
)
def test_capacity_unit_canonicalisation(raw: str, expected: str) -> None:
    assert canonical_capacity_unit(raw) == expected


def test_pressure_unit_canonicalisation() -> None:
    assert canonical_pressure_unit("bar") == "barg"
    assert canonical_pressure_unit("barg") == "barg"


def test_scmd_roundtrip() -> None:
    assert convert_capacity(1000, "Nm3/hr", "SCMD") == 1000 * SCMD_PER_NM3HR
    assert convert_capacity(24000, "SCMD", "Nm3/hr") == 1000


def test_up_to_sets_max_only() -> None:
    r = parse_capacity("Up to 20,000 Nm3/hr")
    assert r.min is None
    assert r.max == 20000
    assert r.unit == "Nm3/hr"


def test_range_phrase_sets_both_bounds() -> None:
    r = parse_capacity("10000 to 100000 SCMD")
    assert r.min == 10000
    assert r.max == 100000
    assert r.unit == "SCMD"


def test_pressure_up_to() -> None:
    r = parse_pressure("Discharge up to 1000 Barg")
    assert r.min is None
    assert r.max == 1000
    assert r.unit == "barg"


def test_single_value_is_a_point() -> None:
    r = parse_capacity("25000 Nm3/hr")
    assert r.min == 25000
    assert r.max == 25000


def test_dash_range() -> None:
    r = parse_capacity("10000–100000 SCMD")
    assert (r.min, r.max) == (10000, 100000)
