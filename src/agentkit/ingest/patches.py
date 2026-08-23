"""Deterministic per-document patch step.

If ``clients/<client>/patches/<sha256>.patch`` exists, it is applied to the
converted Markdown after conversion. The patch is a standard unified diff; the
applier is intentionally small (context/`-`/`+` hunks) and raises on a hunk that
does not line up, so a stale patch fails loudly rather than corrupting output.
The patches directory is empty for now — this is the hook the review milestone
will use for hand corrections.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CLIENTS_DIR = _REPO_ROOT / "clients"

_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def patch_path(client: str, sha256: str, *, clients_dir: Path | None = None) -> Path:
    base = clients_dir or _CLIENTS_DIR
    return base / client / "patches" / f"{sha256}.patch"


def apply_unified_diff(text: str, diff: str) -> str:
    """Apply a unified diff to ``text``. Raises ``ValueError`` on a mismatch."""
    src = text.splitlines()
    out: list[str] = []
    i = 0  # cursor into src
    lines = diff.splitlines()
    di = 0
    while di < len(lines):
        m = _HUNK.match(lines[di])
        if not m:
            di += 1
            continue
        start = int(m.group(1)) - 1
        if start < i:
            raise ValueError(f"overlapping/backwards hunk at source line {start + 1}")
        out.extend(src[i:start])
        i = start
        di += 1
        while di < len(lines) and not lines[di].startswith("@@"):
            h = lines[di]
            if h.startswith("-"):
                if i >= len(src) or src[i] != h[1:]:
                    raise ValueError(f"context mismatch removing line {i + 1}: {h[1:]!r}")
                i += 1
            elif h.startswith("+"):
                out.append(h[1:])
            else:  # ' ' context or bare blank line
                ctx = h[1:] if h.startswith(" ") else h
                if i >= len(src) or src[i] != ctx:
                    raise ValueError(f"context mismatch at line {i + 1}: {ctx!r}")
                out.append(src[i])
                i += 1
            di += 1
    out.extend(src[i:])
    tail = "\n" if text.endswith("\n") else ""
    return "\n".join(out) + tail


def apply_patch_if_present(
    markdown: str, client: str, sha256: str, *, clients_dir: Path | None = None
) -> tuple[str, bool]:
    """Return ``(markdown, applied)`` — unchanged when no patch file exists."""
    path = patch_path(client, sha256, clients_dir=clients_dir)
    if not path.exists():
        return markdown, False
    return apply_unified_diff(markdown, path.read_text()), True
