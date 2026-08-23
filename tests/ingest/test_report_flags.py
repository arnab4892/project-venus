"""Content-based exclusion CANDIDATES surfaced by the conversion report.

Per the milestone decision, nothing is auto-excluded; the report only flags
near-duplicate, near-empty and nav-orphaned pages for a human to decide.
"""

from __future__ import annotations

from agentkit.ingest.pipeline import near_duplicate_pairs, near_empty_pages, nav_orphans


def test_near_empty_flags_thin_pages():
    bodies = {"a": "word " * 100, "b": "tiny"}
    assert near_empty_pages(bodies, min_chars=50) == ["b"]


def test_near_duplicate_flags_high_overlap():
    shared = "process gas compressor catalogue download pdf hydrogen oxygen"
    bodies = {
        "catalogue1": shared + " extra one",
        "catalogue-testing": shared + " extra two",
        "about": "who we are pioneering industrial compression company",
    }
    pairs = near_duplicate_pairs(bodies, threshold=0.8)
    flat = {frozenset(p) for p in pairs}
    assert frozenset({"catalogue1", "catalogue-testing"}) in flat
    assert frozenset({"catalogue1", "about"}) not in flat


def test_nav_orphans_are_pages_off_the_primary_nav():
    pages = ["index.php", "about.html", "cng-boosters.html"]
    nav = {"index.php", "about.html"}
    assert nav_orphans(pages, nav) == ["cng-boosters.html"]
