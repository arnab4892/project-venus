"""Deterministic per-document patch step (LLD-ING-04 finisher)."""

from __future__ import annotations

import pytest

from agentkit.ingest.patches import apply_patch_if_present, apply_unified_diff


def test_no_patch_leaves_markdown_unchanged(tmp_path):
    md = "# Title\n\nBody.\n"
    out, applied = apply_patch_if_present(md, "jyotech", "deadbeef", clients_dir=tmp_path)
    assert out == md
    assert applied is False


def test_unified_diff_applies():
    text = "line one\nline two\nline three\n"
    diff = "@@ -2,1 +2,1 @@\n-line two\n+line TWO\n"
    assert apply_unified_diff(text, diff) == "line one\nline TWO\nline three\n"


def test_context_mismatch_raises_loudly():
    text = "alpha\nbeta\n"
    diff = "@@ -1,1 +1,1 @@\n-gamma\n+delta\n"
    with pytest.raises(ValueError):
        apply_unified_diff(text, diff)
