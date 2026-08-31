"""Number presentation for answer text (voice polish).

Tool values arrive as ``Decimal``/``float`` (``25000``, ``25000.0``) and, rendered raw, read as
spec-sheet noise ("25000.0 Nm3/hr"). :func:`format_number` drops spurious trailing decimals and
groups values ≥ 10,000 Indian-style (25,000 · 1,00,000 · 10,00,000). It touches **only the
number** — the caller keeps the unit exactly as published. The value is never changed, only its
typography, so the grounding numeric guard (which compares on numeric value) is unaffected.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

_GROUP_THRESHOLD = 10_000


def _indian_group(integer: str) -> str:
    """Group an integer string Indian-style: last 3 digits, then pairs (12,34,567)."""
    neg, s = (integer.startswith("-")), integer.lstrip("-")
    if len(s) <= 3:
        return ("-" if neg else "") + s
    last3, rest = s[-3:], s[:-3]
    parts: list[str] = []
    while len(rest) > 2:
        parts.insert(0, rest[-2:])
        rest = rest[:-2]
    if rest:
        parts.insert(0, rest)
    return ("-" if neg else "") + ",".join(parts) + "," + last3


def format_number(value) -> str:
    """Render a tool numeric for prose: no spurious ``.0``; Indian grouping for ≥ 10,000.

    Non-numeric input is returned as ``str(value)`` unchanged (so a caller can format blindly).
    Meaningful decimals are preserved (``12.5`` → ``12.5``); only all-zero fractions are dropped.
    """
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    if dec == dec.to_integral_value():
        i = int(dec)
        return _indian_group(str(i)) if abs(i) >= _GROUP_THRESHOLD else str(i)
    # keep the fractional part; group only the integer part
    sign = "-" if dec < 0 else ""
    whole, _, frac = format(abs(dec), "f").partition(".")
    frac = frac.rstrip("0")
    grouped = _indian_group(whole) if int(whole) >= _GROUP_THRESHOLD else whole
    return f"{sign}{grouped}" + (f".{frac}" if frac else "")
