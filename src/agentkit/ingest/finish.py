"""Common finisher (LLD-ING-04): Unicode cleanup, front-matter, output path.

The one hard rule: engineering units such as ``Nm³/hr`` must survive intact, so
cleanup uses **NFC** normalisation only — never NFKC, which folds ³ → 3. We also
drop zero-width characters and normalise non-breaking spaces.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DATA_ROOT = _REPO_ROOT / "data"

_NBSP = chr(0x00A0)
# Zero-width space / non-joiner / joiner / BOM — invisible noise to strip.
_ZERO_WIDTH = dict.fromkeys([0x200B, 0x200C, 0x200D, 0xFEFF], None)

_FRONT_MATTER_KEYS = ("url", "kind", "sha256", "fetched_at", "title")


def clean_unicode(text: str) -> str:
    """NFC-normalise, strip zero-width chars, and turn NBSP into a plain space."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace(_NBSP, " ")
    return text.translate(_ZERO_WIDTH)


_IMAGE_RUN = re.compile(r"(?:<!-- image -->\s*){2,}")


def tidy_markdown(md: str) -> str:
    """Collapse consecutive ``<!-- image -->`` placeholders to one; drop orphan
    single-character lines (extraction noise). Table rows (``|``) are preserved.
    Applies to both HTML and PDF output.
    """
    md = _IMAGE_RUN.sub("<!-- image -->\n\n", md)
    kept = [
        ln
        for ln in md.split("\n")
        if not (len(ln.strip()) == 1 and not ln.lstrip().startswith("|"))
    ]
    return "\n".join(kept)


def build_front_matter(meta: dict) -> str:
    """Render the five-field YAML front-matter block."""
    lines = ["---"]
    for key in _FRONT_MATTER_KEYS:
        value = meta.get(key)
        # JSON scalars are valid YAML; ensure_ascii=False keeps ³ intact.
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def render_markdown(body: str, meta: dict) -> str:
    """Front-matter followed by the cleaned Markdown body."""
    return build_front_matter(meta) + "\n" + clean_unicode(body)


def output_path(client: str, sha256: str, *, data_root: Path | None = None) -> Path:
    """``<data_root>/<client>/md/<sha256>.md`` (default data root = repo ``data/``)."""
    root = data_root or _DEFAULT_DATA_ROOT
    return Path(root) / client / "md" / f"{sha256}.md"


def manifest_path(client: str, *, data_root: Path | None = None) -> Path:
    """``<data_root>/<client>/ingest-manifest.json`` — url→sha map for the verifier."""
    root = data_root or _DEFAULT_DATA_ROOT
    return Path(root) / client / "ingest-manifest.json"


def raw_path(client: str, sha256: str, kind: str, *, data_root: Path | None = None) -> Path:
    """``<data_root>/<client>/raw/<sha256>.<ext>`` — the cached source bytes.

    Cached at ingest time so ``agentkit ingest verify`` has a deterministic,
    offline witness (e.g. ``pdftotext`` reads it) without re-fetching.
    """
    root = data_root or _DEFAULT_DATA_ROOT
    ext = "pdf" if kind == "pdf" else "html"
    return Path(root) / client / "raw" / f"{sha256}.{ext}"
