"""Common finisher (LLD-ING-04): Unicode cleanup + front-matter.

The single hard rule: `Nm³/hr` must survive intact — NFC only, never NFKC
(which folds ³ → 3). Front-matter must carry the five required fields.
"""

from __future__ import annotations

from agentkit.ingest.finish import build_front_matter, clean_unicode, render_markdown


def test_nm3_survives_unmangled():
    assert clean_unicode("Capacity up to 20,000 Nm³/hr") == "Capacity up to 20,000 Nm³/hr"
    # explicitly NOT folded to the ASCII '3' form
    assert "Nm3/hr" not in clean_unicode("Nm³/hr")
    assert "³" in clean_unicode("Nm³/hr")


def test_zero_width_and_nbsp_cleaned():
    assert clean_unicode("hydrogen​ duty") == "hydrogen duty"
    assert clean_unicode("20,000 Nm³/hr") == "20,000 Nm³/hr"
    assert clean_unicode("bom﻿free") == "bomfree"


def test_front_matter_has_all_five_fields():
    fm = build_front_matter(
        {
            "url": "https://www.jyotech.com/about.html",
            "kind": "html",
            "sha256": "abc123",
            "fetched_at": "2026-08-23T10:00:00+00:00",
            "title": "About Us",
        }
    )
    assert fm.startswith("---")
    for key in ("url", "kind", "sha256", "fetched_at", "title"):
        assert f"{key}:" in fm


def test_render_markdown_is_front_matter_then_body():
    out = render_markdown(
        "# Body\n\nNm³/hr here.",
        {
            "url": "u",
            "kind": "html",
            "sha256": "s",
            "fetched_at": "t",
            "title": "T",
        },
    )
    assert out.startswith("---\n")
    assert out.count("---") >= 2
    assert out.rstrip().endswith("Nm³/hr here.")
