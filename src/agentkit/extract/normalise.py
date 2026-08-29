"""Numeric / unit normalisation (LLD-EXT-09).

The catalogue mixes ``Nm³/hr`` and ``SCMD`` for flow and ``bar``/``barg`` for
pressure. This module is the single place those are canonicalised, and it owns
the documented SCMD↔Nm³/hr constant used to compare a query against a
capability row stored in the other unit.

Units were the first slice; the extractor milestone (3a) adds the **range
parsing** used after typed extraction: a printed phrase such as
``"up to 20,000 Nm3/hr"`` or ``"10000–100000 SCMD"`` becomes a structured
``(min, max, unit)`` in Python (never in the prompt) before it is staged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Documented constant: 1 SCMD ≈ 1/24 Nm3/hr, i.e. 1 Nm3/hr = 24 SCMD
# (SCMD = standard cubic metres per *day*; Nm3/hr is per *hour*; 24 h/day).
SCMD_PER_NM3HR = 24

CAPACITY_UNITS = ("Nm3/hr", "SCMD")
PRESSURE_UNIT = "barg"

# The flow family — the ONLY capacity units with a documented interconversion
# (SCMD ↔ Nm3/hr). match_capability may compare a query across these two.
_CAPACITY_ALIASES = {
    "nm3/hr": "Nm3/hr",
    "nm3/h": "Nm3/hr",
    "nm3hr": "Nm3/hr",
    "nm3/hour": "Nm3/hr",
    "scmd": "SCMD",
}

# Extended measure vocabulary (LLD-TOOL-01 design note). These units are *recognised* so a
# row carrying one is comparable to a query in the SAME unit — but there is no invented
# cross-unit factor, so any pair of different canonical units is non-comparable. Covers the
# units the F&S / diving / process rows actually print (cfm, lpm, lumen, tons, TPD, kg/hr,
# W, kg/cm2g, m3/hr, SCMH).
_MEASURE_ALIASES = {
    "m3/hr": "m3/hr", "m3/h": "m3/hr", "m3hr": "m3/hr",
    "scmh": "SCMH",
    "cfm": "cfm",
    "lpm": "lpm", "l/min": "lpm",
    "lumen": "lumen", "lumens": "lumen", "lm": "lumen",
    "tons": "tons", "ton": "tons", "tonnes": "tons",
    "tpd": "TPD",
    "kg/hr": "kg/hr", "kg/h": "kg/hr", "kghr": "kg/hr",
    "w": "W", "watt": "W", "watts": "W",
}

_PRESSURE_ALIASES = {
    "bar": PRESSURE_UNIT, "barg": PRESSURE_UNIT,
    "psi": "psi", "psig": "psi",
    "kg/cm2g": "kg/cm2g", "kg/cm2": "kg/cm2g",
}


def canonical_capacity_unit(unit: str) -> str:
    """Canonicalise a capacity/measure unit. Raises ``ValueError`` on unknown units.

    ``Nm³/hr | Nm3/hr | NM3/HR → Nm3/hr``; ``SCMD`` kept; the extended vocabulary
    (``cfm``, ``lpm``, ``lumen``, ``tons``, ``TPD``, ``kg/hr``, ``W``, ``m3/hr``, ``SCMH`` …)
    canonicalises to itself — comparable only within its own unit (see :func:`convert_capacity`).
    """
    key = re.sub(r"\s+", "", unit.replace("³", "3")).lower()
    if key in _CAPACITY_ALIASES:
        return _CAPACITY_ALIASES[key]
    if key in _MEASURE_ALIASES:
        return _MEASURE_ALIASES[key]
    raise ValueError(f"Unknown capacity unit: {unit!r}")


def canonical_pressure_unit(unit: str) -> str:
    """``bar | barg → barg``; ``psi``/``kg/cm2g`` canonicalise to themselves. Raises on unknown.

    Non-``barg`` pressure units are recognised but never converted to ``barg`` (no invented
    factor), so match_capability treats them as non-comparable rather than crashing.
    """
    key = re.sub(r"\s+", "", unit.replace("²", "2")).lower()
    try:
        return _PRESSURE_ALIASES[key]
    except KeyError:
        raise ValueError(f"Unknown pressure unit: {unit!r}") from None


def convert_capacity(value: float, from_unit: str, to_unit: str) -> float:
    """Convert a flow ``value`` between capacity units.

    Only the documented flow-family pair converts: Nm3/hr → SCMD multiplies by
    :data:`SCMD_PER_NM3HR`, SCMD → Nm3/hr divides. Identical canonical units pass through.
    Any other pair (e.g. ``cfm`` ↔ ``Nm3/hr``) raises — no cross-unit factor is invented, so
    the caller treats it as non-comparable.
    """
    src = canonical_capacity_unit(from_unit)
    dst = canonical_capacity_unit(to_unit)
    if src == dst:
        return float(value)
    if src == "Nm3/hr" and dst == "SCMD":
        return float(value) * SCMD_PER_NM3HR
    if src == "SCMD" and dst == "Nm3/hr":
        return float(value) / SCMD_PER_NM3HR
    raise ValueError(f"No conversion from {src} to {dst}")


# ---------------------------------------------------------------------------
# Range parsing (LLD-EXT-09, extraction milestone): printed phrase → (min,max,unit)
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_RANGE_SEP_RE = re.compile(r"\d\s*(?:to|–|—|-|…|\.\.\.)\s*\d", re.IGNORECASE)
_UPTO_RE = re.compile(r"up\s*to|upto|max(?:imum)?|≤|<=", re.IGNORECASE)

_CAPACITY_TOKEN_RE = re.compile(r"nm\s*3\s*/?\s*h(?:r|our)?|nm³/hr|scmd", re.IGNORECASE)
_PRESSURE_TOKEN_RE = re.compile(r"barg?|bar\b", re.IGNORECASE)


@dataclass
class Range:
    """A parsed numeric envelope. ``min``/``max`` are ``None`` when not stated."""

    min: float | None
    max: float | None
    unit: str | None


def _strip_units(phrase: str) -> str:
    """Blank out unit tokens so their embedded digits (the ``3`` in ``Nm3``) don't
    get mistaken for values."""
    phrase = _CAPACITY_TOKEN_RE.sub(" ", phrase)
    return _PRESSURE_TOKEN_RE.sub(" ", phrase)


def _numbers(phrase: str) -> list[float]:
    return [float(m.group(0).replace(",", "")) for m in _NUMBER_RE.finditer(phrase)]


def _bounds(phrase: str) -> tuple[float | None, float | None]:
    """Apply the LLD-EXT-09 rule: ``"up to X" → (None, X)``; ranges → ``(lo, hi)``.

    Unit tokens are stripped first so a unit's embedded digit is never read as a
    value. The range separator is checked on the unit-stripped phrase too.
    """
    clean = _strip_units(phrase)
    nums = _numbers(clean)
    if not nums:
        return None, None
    if len(nums) >= 2 and _RANGE_SEP_RE.search(clean):
        lo, hi = min(nums[0], nums[1]), max(nums[0], nums[1])
        return lo, hi
    if _UPTO_RE.search(phrase):
        return None, nums[-1]
    # A single definite value with no "up to" — a point on the envelope.
    return nums[0], nums[0]


def parse_capacity(phrase: str) -> Range:
    """Parse a flow phrase, e.g. ``"Up to 20,000 Nm3/hr"`` → ``Range(None, 20000, 'Nm3/hr')``."""
    lo, hi = _bounds(phrase)
    unit = None
    m = _CAPACITY_TOKEN_RE.search(phrase)
    if m:
        try:
            unit = canonical_capacity_unit(m.group(0))
        except ValueError:
            unit = None
    return Range(lo, hi, unit)


def parse_pressure(phrase: str) -> Range:
    """Parse a pressure phrase, e.g. ``"Discharge up to 1000 Barg"`` → ``Range(None, 1000, 'barg')``."""
    lo, hi = _bounds(phrase)
    unit = None
    if _PRESSURE_TOKEN_RE.search(phrase):
        unit = PRESSURE_UNIT
    return Range(lo, hi, unit)
