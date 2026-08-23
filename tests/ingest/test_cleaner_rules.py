"""HTML cleaner rules + markdown tidy (LLD-ING-02 / milestone-2 goal items 3, 6).

Each banned junk pattern has a rule; rules are generic in code, selectors/values
per-client. Verified against a junk fixture modelled on portable Compressor.html.
"""

from __future__ import annotations

from pathlib import Path

from agentkit.ingest.finish import tidy_markdown
from agentkit.ingest.html_convert import html_to_markdown

FIXTURES = Path(__file__).parent / "fixtures"

STRIP = ["nav", "header", "script", "style", "iframe", ".breadcrumb", ".blog-item"]
DROP_ALT = ["Image"]
DROP_LINK = ["Read More"]


def _convert():
    html = (FIXTURES / "sample_junk.html").read_bytes()
    return html_to_markdown(
        html,
        strip_selectors=STRIP,
        base_url="https://www.jyotech.com/portable%20Compressor.html",
        drop_alt_text=DROP_ALT,
        drop_link_text=DROP_LINK,
    )


def test_breadcrumb_list_stripped():
    md = _convert().markdown
    assert "Home" not in md.split("PORTABLE")[0]  # breadcrumb crumb gone
    assert "Portable Compressor\n" not in md      # breadcrumb crumb, not the H1


def test_bare_image_alt_dropped():
    md = _convert().markdown
    # the placeholder alt "Image" is gone, real alt text (if any) would remain
    assert "\nImage\n" not in f"\n{md}\n"
    assert not any(line.strip() == "Image" for line in md.splitlines())


def test_read_more_heading_and_anchor_dropped():
    md = _convert().markdown
    assert "Read More" not in md
    assert "read more" not in md.lower()


def test_teaser_cards_stripped_but_real_content_kept():
    md = _convert().markdown
    # teaser card product names (inside .blog-item) are gone …
    assert "Compact & Super Silent Machine" not in md
    assert "Low Pressure Breathing Air System" not in md
    # … while the page's own content survives
    assert "# PORTABLE COMPRESSORS" in md
    assert "300 lpm" in md
    assert "C-Monitor" in md  # accessory heading kept (only the Read More stub dropped)


def test_tidy_collapses_consecutive_image_placeholders():
    md = "A\n\n<!-- image -->\n\n<!-- image -->\n\n<!-- image -->\n\nB"
    out = tidy_markdown(md)
    assert out.count("<!-- image -->") == 1


def test_tidy_drops_orphan_single_char_lines_but_keeps_table_rows():
    md = "Heading\n\nI\n\n| a | b |\n| --- | --- |\n\nBody"
    out = tidy_markdown(md)
    assert "\nI\n" not in f"\n{out}\n"
    assert "| a | b |" in out           # table row preserved
    assert "| --- | --- |" in out
