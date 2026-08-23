"""Conversion verifier — ``agentkit ingest verify`` (LLD-ING, milestone-2 addition).

An independent check that conversion did not silently lose or mangle content:

* **PDF sources** — extract a token multiset (numbers, units, standard names,
  model names) from an independent ``pdftotext`` witness of the cached raw bytes,
  and assert every witness token (with multiplicity) also appears in the
  converted Markdown. Image-only content is invisible to ``pdftotext`` too, so
  the witness never demands tokens that live only in pictures — the check stays
  honest.
* **HTML sources** — assert none of the six banned junk patterns remain.

Per-source PASS or the exact missing/mangled tokens; non-zero exit on any
failure. This is a new LLD-level capability (see milestone-2 notes) to be folded
into LLD-ING via a `/prd-change`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .finish import manifest_path
from .sources import Sources, kind_for, load_sources

# --------------------------------------------------------------------------- #
# Token extraction (independent-witness comparison)
# --------------------------------------------------------------------------- #
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_UNIT = re.compile(r"(?i)\b(?:nm3/hr|barg|bar|lpm|kw|hp)\b")
_ISO = re.compile(r"(?i)\bISO\s*\d+:\d+")
_API = re.compile(r"(?i)\bAPI[-\s]?\d+")
_ENNFPA = re.compile(r"(?i)\b(?:EN|NFPA)\b")
_MCH = re.compile(r"(?i)\bMCH[-\s]?\d+[\w/]*")
_NAMED_MODEL = re.compile(r"(?i)\b(?:ICON|VEGA|NOVA|NEPTUNE|PROEYE)\b")


def _norm_number(tok: str) -> str:
    return tok.replace(",", "")


def _norm_model(tok: str) -> str:
    return re.sub(r"[\s-]", "", tok.upper())


def extract_tokens(text: str) -> Counter:
    """Return the multiset of witness tokens found in ``text`` (normalised)."""
    c: Counter = Counter()
    c.update(_norm_number(t) for t in _NUMBER.findall(text))
    c.update(t.lower() for t in _UNIT.findall(text))
    c.update(re.sub(r"\s+", "", t.upper()) for t in _ISO.findall(text))
    c.update(re.sub(r"[\s-]", "", t.upper()) for t in _API.findall(text))
    c.update(t.upper() for t in _ENNFPA.findall(text))
    c.update(_norm_model(t) for t in _MCH.findall(text))
    c.update(t.upper() for t in _NAMED_MODEL.findall(text))
    return c


def pdftotext(raw_pdf: Path) -> str:
    """Independent text witness for a PDF via poppler's ``pdftotext``."""
    if shutil.which("pdftotext") is None:
        raise RuntimeError(
            "pdftotext (poppler) not found — required by `agentkit ingest verify`. "
            "Install it (macOS: `brew install poppler`)."
        )
    out = subprocess.run(
        ["pdftotext", str(raw_pdf), "-"], capture_output=True, text=True, check=True
    )
    return out.stdout


# --------------------------------------------------------------------------- #
# HTML banned junk patterns (the six known specimens)
# --------------------------------------------------------------------------- #
BANNED_PATTERNS: dict[str, re.Pattern] = {
    "breadcrumb_list": re.compile(r"(?mi)^\s*(?:\d+\.\s*)?home\s*>"),
    "bare_image_node": re.compile(r"(?mi)^\s*Image\s*$"),
    "read_more_stub": re.compile(r"(?mi)^\s*read\s*more\s*$"),
    "teaser_card_link": re.compile(r"(?i)read\s*more"),
    "consecutive_image_placeholders": re.compile(r"(?:<!-- image -->\s*){2,}"),
    "orphan_single_char_line": re.compile(r"(?m)^[ \t]*[A-Za-z0-9][ \t]*$"),
}


# --------------------------------------------------------------------------- #
# Report types
# --------------------------------------------------------------------------- #
@dataclass
class SourceVerdict:
    url: str
    kind: str
    ok: bool
    missing_tokens: list[str] = field(default_factory=list)   # PDF: witness − md
    junk_hits: list[str] = field(default_factory=list)        # HTML: banned pattern names
    note: str = ""


@dataclass
class VerifyReport:
    client: str
    verdicts: list[SourceVerdict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(v.ok for v in self.verdicts)

    def lines(self) -> list[str]:
        out = [f"ingest verify — client={self.client}"]
        for v in self.verdicts:
            if v.ok:
                out.append(f"  PASS  [{v.kind}] {v.url}" + (f"  ({v.note})" if v.note else ""))
            elif v.kind == "pdf":
                shown = ", ".join(v.missing_tokens[:25])
                more = "" if len(v.missing_tokens) <= 25 else f" (+{len(v.missing_tokens) - 25} more)"
                out.append(f"  FAIL  [pdf] {v.url}")
                out.append(f"          missing/mangled tokens: {shown}{more}")
            else:
                out.append(f"  FAIL  [html] {v.url}")
                out.append(f"          banned junk patterns present: {', '.join(v.junk_hits)}")
        n_fail = sum(1 for v in self.verdicts if not v.ok)
        out.append(f"  {'ALL PASS' if self.ok else str(n_fail) + ' FAILED'} / {len(self.verdicts)} sources")
        return out


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def missing_witness_tokens(witness_text: str, md_text: str) -> list[str]:
    """Multiset of witness tokens (with multiplicity) absent from ``md_text``."""
    missing = extract_tokens(witness_text) - extract_tokens(md_text)
    return sorted(f"{tok}x{cnt}" if cnt > 1 else tok for tok, cnt in missing.items())


def check_pdf(url: str, raw_pdf: Path, md_text: str) -> SourceVerdict:
    witness_text = pdftotext(raw_pdf)
    missing_list = missing_witness_tokens(witness_text, md_text)
    return SourceVerdict(
        url=url, kind="pdf", ok=(not missing_list), missing_tokens=missing_list,
        note=f"{sum(extract_tokens(witness_text).values())} witness tokens" if not missing_list else "",
    )


def check_html(url: str, md_text: str) -> SourceVerdict:
    hits = [name for name, pat in BANNED_PATTERNS.items() if pat.search(md_text)]
    return SourceVerdict(url=url, kind="html", ok=(not hits), junk_hits=hits, note="clean" if not hits else "")


def run_verify(client: str | None = None, *, sources: Sources | None = None, data_root: Path | None = None) -> VerifyReport:
    """Verify every active source's converted Markdown against its witness.

    Uses the ingest manifest (url→sha) so byte-identical duplicate URLs both
    resolve to the one converted file, and no re-fetch is needed.
    """
    src = sources or load_sources(client)
    client = src.client
    report = VerifyReport(client=client)

    mpath = manifest_path(client, data_root=data_root)
    if not mpath.exists():
        report.verdicts.append(
            SourceVerdict(url="(manifest)", kind="?", ok=False, note=f"no {mpath.name} — run `ingest run` first")
        )
        return report
    by_url = {e["url"]: e for e in json.loads(mpath.read_text())}

    for url in src.active_pages() + src.active_pdfs():
        report.verdicts.append(_verify_one(url, kind_for(url), by_url.get(url)))
    return report


def _verify_one(url: str, kind: str, entry: dict | None) -> SourceVerdict:
    if entry is None:
        return SourceVerdict(url=url, kind=kind, ok=False, note="not in manifest — run `ingest run`")
    mdp = Path(entry["md_path"])
    if not mdp.exists():
        return SourceVerdict(url=url, kind=kind, ok=False, note="no converted Markdown found — run `ingest run`")
    md_text = mdp.read_text(encoding="utf-8")
    if kind == "pdf":
        rawp = Path(entry["raw_path"])
        if not rawp.exists():
            return SourceVerdict(url=url, kind=kind, ok=False, note="no cached raw PDF — run `ingest run`")
        return check_pdf(url, rawp, md_text)
    return check_html(url, md_text)
