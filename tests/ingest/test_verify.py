"""Tests for the conversion verifier (`agentkit ingest verify`).

Pure-function tests — no network, no pdftotext, no Docling. The token witness
comparison is exercised via ``missing_witness_tokens`` and the HTML junk scan via
``check_html`` / ``BANNED_PATTERNS``.
"""

from __future__ import annotations

from agentkit.ingest.verify import (
    BANNED_PATTERNS,
    check_html,
    extract_tokens,
    missing_witness_tokens,
)


def test_extract_tokens_covers_numbers_units_standards_models():
    text = "Up to 20,000 Nm3/hr at 350 Barg, API-618, ISO 9001:2015, MCH-16 NOVA, 110 HP, EN NFPA"
    toks = extract_tokens(text)
    assert toks["20000"] == 1          # comma normalised
    assert toks["nm3/hr"] == 1
    assert toks["barg"] == 1
    assert toks["hp"] == 1
    assert toks["API618"] == 1         # hyphen/space normalised
    assert toks["ISO9001:2015"] == 1
    assert toks["MCH16"] == 1          # alias-normalised
    assert toks["NOVA"] == 1
    assert toks["EN"] == 1 and toks["NFPA"] == 1


def test_bar_not_matched_inside_barg():
    # 'Barg' must not also yield a spurious 'bar' token.
    assert extract_tokens("350 Barg")["bar"] == 0


def test_missing_tokens_flags_truncation_and_loss():
    witness = "ISO 45001:2018 and 25000 Nm3/hr, 110 HP"
    truncated = "ISO 45001:201 and 25000 Nm3/hr"  # lost :2018 and one HP
    missing = missing_witness_tokens(witness, truncated)
    assert "ISO45001:2018" in missing
    assert "2018" in missing
    assert "hp" in missing


def test_no_missing_when_all_present():
    witness = "20000 Nm3/hr 350 Barg API-618"
    md = "Capacity 20,000 Nm3/hr up to 350 Barg per API-618 spec"
    assert missing_witness_tokens(witness, md) == []


def test_banned_patterns_each_detected():
    assert BANNED_PATTERNS["breadcrumb_list"].search("1. Home > Products")
    assert BANNED_PATTERNS["bare_image_node"].search("\nImage\n")
    assert BANNED_PATTERNS["read_more_stub"].search("\nRead More\n")
    assert BANNED_PATTERNS["teaser_card_link"].search("##### Read More")
    assert BANNED_PATTERNS["consecutive_image_placeholders"].search("<!-- image -->\n\n<!-- image -->")
    assert BANNED_PATTERNS["orphan_single_char_line"].search("\nI\n")


def test_check_html_pass_and_fail():
    clean = "# Product\n\nReciprocating compressor up to 20000 Nm3/hr.\n"
    assert check_html("u", clean).ok

    dirty = "# Product\n\n##### Read More\n"
    verdict = check_html("u", dirty)
    assert not verdict.ok
    assert "teaser_card_link" in verdict.junk_hits


def test_table_pipe_row_is_not_an_orphan_char_line():
    # a table cell line starting with '|' must not be flagged as orphan single char
    assert not BANNED_PATTERNS["orphan_single_char_line"].search("| a |")
