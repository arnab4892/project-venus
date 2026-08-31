"""format_number (voice polish): drop spurious decimals, Indian grouping ≥ 10,000."""

from __future__ import annotations

from decimal import Decimal

import pytest

from agentkit.runtime.format import format_number


@pytest.mark.parametrize(
    "value,expected",
    [
        (25000.0, "25,000"),      # spurious trailing decimal dropped + grouped
        (Decimal("25000"), "25,000"),
        (25000, "25,000"),
        ("25000.0", "25,000"),
        (1000, "1000"),           # below the 10,000 grouping threshold
        (350, "350"),
        (100000, "1,00,000"),     # Indian lakh grouping
        (2500000, "25,00,000"),
        (1000.0, "1000"),
        (12.5, "12.5"),           # meaningful fraction preserved, no grouping
        (24000.0, "24,000"),
        (0, "0"),
    ],
)
def test_format_number(value, expected):
    assert format_number(value) == expected


def test_non_numeric_passes_through():
    assert format_number("N/A") == "N/A"
    assert format_number(None) == "None"
