"""Numeric / unit normalisation (LLD-EXT-09).

The catalogue mixes ``Nm³/hr`` and ``SCMD`` for flow and ``bar``/``barg`` for
pressure. This module is the single place those are canonicalised, and it owns
the documented SCMD↔Nm³/hr constant used to compare a query against a
capability row stored in the other unit.

This is the first slice of LLD-EXT-09 (units); the extractor milestone extends
it with "up to X" range parsing.
"""

from __future__ import annotations

import re

# Documented constant: 1 SCMD ≈ 1/24 Nm3/hr, i.e. 1 Nm3/hr = 24 SCMD
# (SCMD = standard cubic metres per *day*; Nm3/hr is per *hour*; 24 h/day).
SCMD_PER_NM3HR = 24

CAPACITY_UNITS = ("Nm3/hr", "SCMD")
PRESSURE_UNIT = "barg"

_CAPACITY_ALIASES = {
    "nm3/hr": "Nm3/hr",
    "nm3/h": "Nm3/hr",
    "nm3hr": "Nm3/hr",
    "nm3/hour": "Nm3/hr",
    "scmd": "SCMD",
}
_PRESSURE_ALIASES = {"bar": PRESSURE_UNIT, "barg": PRESSURE_UNIT}


def canonical_capacity_unit(unit: str) -> str:
    """``Nm³/hr | Nm3/hr | NM3/HR → Nm3/hr``; ``SCMD`` kept. Raises on unknown units."""
    key = re.sub(r"\s+", "", unit.replace("³", "3")).lower()
    try:
        return _CAPACITY_ALIASES[key]
    except KeyError:
        raise ValueError(f"Unknown capacity unit: {unit!r}") from None


def canonical_pressure_unit(unit: str) -> str:
    """``bar | barg → barg``. Raises on unknown units."""
    key = unit.strip().lower()
    try:
        return _PRESSURE_ALIASES[key]
    except KeyError:
        raise ValueError(f"Unknown pressure unit: {unit!r}") from None


def convert_capacity(value: float, from_unit: str, to_unit: str) -> float:
    """Convert a flow ``value`` between canonical capacity units.

    Nm3/hr → SCMD multiplies by :data:`SCMD_PER_NM3HR`; SCMD → Nm3/hr divides.
    """
    src = canonical_capacity_unit(from_unit)
    dst = canonical_capacity_unit(to_unit)
    if src == dst:
        return float(value)
    if src == "Nm3/hr" and dst == "SCMD":
        return float(value) * SCMD_PER_NM3HR
    if src == "SCMD" and dst == "Nm3/hr":
        return float(value) / SCMD_PER_NM3HR
    raise ValueError(f"No conversion from {src} to {dst}")  # pragma: no cover
