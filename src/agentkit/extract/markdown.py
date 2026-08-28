"""Read converted Markdown and split it into sections (LLD-EXT-01).

The ingest milestone writes ``data/<client>/md/<sha256>.md`` with five-field YAML
front-matter (``url, kind, sha256, fetched_at, title``) and, for PDFs, ``<!-- page
N -->`` markers between pages. This module is the reader side ingest never grew:
it parses that front-matter and splits the body on **H1–H3** headings into
:class:`Section`s, each carrying ``doc_id`` (via :func:`agentkit.ingest.sources.
doc_id_for`), ``heading_path``, a page range (PDF only), and the verbatim section
text used later by the evidence gate.

Framework-generic — no client strings.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from agentkit.ingest.finish import output_path
from agentkit.ingest.sources import doc_id_for

_FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_PAGE_MARKER_RE = re.compile(r"^<!--\s*page\s+(\d+)\s*-->\s*$", re.MULTILINE)

# Only H1–H3 break sections; deeper headings stay inside the section body.
_MAX_SPLIT_LEVEL = 3


@dataclass
class Section:
    """One heading-delimited slice of a converted document."""

    section_id: str
    doc_id: str
    kind: str  # html | pdf
    url: str | None
    heading_path: list[str]
    page_start: int | None
    page_end: int | None
    text: str

    @property
    def evidence_text(self) -> str:
        """Full verbatim text for the evidence gate = heading(s) + body.

        The structural split moves the section's heading into ``heading_path``, but
        the heading is genuine published source text (and is what the extractor prompt
        shows the model), so a field citing the heading as evidence is grounded. The
        gate must validate against this, not the body alone (LLD-EXT-04).
        """
        return "\n".join([*self.heading_path, self.text])

    @property
    def locator(self) -> str:
        """Human/reviewer locator: page range for PDFs, else the heading path."""
        heading = " › ".join(self.heading_path) if self.heading_path else "(preamble)"
        if self.page_start is not None:
            span = (
                f"p{self.page_start}"
                if self.page_start == self.page_end
                else f"p{self.page_start}-{self.page_end}"
            )
            return f"{span} §{heading}"
        return f"§{heading}"

    def as_record(self) -> dict:
        """JSON-serialisable form for ``sections.json`` (inspectable re-runs)."""
        return {
            "section_id": self.section_id,
            "doc_id": self.doc_id,
            "kind": self.kind,
            "url": self.url,
            "heading_path": self.heading_path,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "locator": self.locator,
            "text": self.text,
        }


def parse_front_matter(md: str) -> tuple[dict, str]:
    """Split ``---``-delimited front-matter from the body. Values are JSON scalars."""
    match = _FRONT_MATTER_RE.match(md)
    if not match:
        return {}, md
    meta: dict = {}
    for line in match.group(1).splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, _, raw = line.partition(":")
        raw = raw.strip()
        try:
            meta[key.strip()] = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            meta[key.strip()] = raw.strip('"')
    return meta, match.group(2)


def split_sections(md: str, *, doc_id: str, kind: str, url: str | None = None) -> list[Section]:
    """Split one converted document's Markdown into H1–H3 sections."""
    _, body = parse_front_matter(md)
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []  # (level, title) for the current heading path
    buf: list[str] = []
    cur_page: int | None = None
    seg_page_start: int | None = None
    seg_page_end: int | None = None
    idx = 0

    def flush() -> None:
        nonlocal idx, seg_page_start, seg_page_end
        text = "\n".join(buf).strip()
        heading_path = [title for _, title in stack]
        # Skip an empty preamble (before the first heading) but keep empty
        # headed sections — the classifier decides what is content.
        if not text and not heading_path:
            buf.clear()
            return
        sections.append(
            Section(
                section_id=f"{doc_id}::s{idx:03d}",
                doc_id=doc_id,
                kind=kind,
                url=url,
                heading_path=heading_path,
                page_start=seg_page_start,
                page_end=seg_page_end,
                text=text,
            )
        )
        idx += 1
        buf.clear()

    for line in body.splitlines():
        page_m = _PAGE_MARKER_RE.match(line)
        if page_m:
            cur_page = int(page_m.group(1))
            if seg_page_start is None:
                seg_page_start = cur_page
            seg_page_end = cur_page
            continue

        heading_m = _HEADING_RE.match(line)
        if heading_m and len(heading_m.group(1)) <= _MAX_SPLIT_LEVEL:
            flush()
            level = len(heading_m.group(1))
            title = heading_m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            # A new section starts at the current page.
            seg_page_start = cur_page
            seg_page_end = cur_page
            continue

        buf.append(line)

    flush()
    return sections


def read_document_sections(path: Path) -> list[Section]:
    """Read one ``.md`` file and split it into sections (front-matter aware)."""
    md = Path(path).read_text(encoding="utf-8")
    meta, _ = parse_front_matter(md)
    url = meta.get("url")
    kind = meta.get("kind") or "html"
    doc_id = doc_id_for(url) if url else f"doc.{Path(path).stem}"
    return split_sections(md, doc_id=doc_id, kind=kind, url=url)


def corpus_sections(client: str, *, data_root: Path | None = None) -> list[Section]:
    """All sections across every converted ``.md`` for a client (deterministic order)."""
    md_dir = output_path(client, "x", data_root=data_root).parent
    out: list[Section] = []
    for md_file in sorted(md_dir.glob("*.md")):
        out.extend(read_document_sections(md_file))
    return out


def document_records(client: str, *, data_root: Path | None = None) -> list[dict]:
    """Identity rows for ``staging.document`` from each converted ``.md`` front-matter.

    ``page_count`` is the highest ``<!-- page N -->`` marker (PDFs) or 1 (HTML).
    ``title``/``division`` are left for the reviewer (front-matter title is often
    the generic site title; division is inferred downstream, not per document).
    """
    md_dir = output_path(client, "x", data_root=data_root).parent
    records: list[dict] = []
    for md_file in sorted(md_dir.glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        meta, body = parse_front_matter(text)
        url = meta.get("url")
        pages = [int(m.group(1)) for m in _PAGE_MARKER_RE.finditer(body)]
        records.append({
            "doc_id": doc_id_for(url) if url else f"doc.{md_file.stem}",
            "kind": meta.get("kind"),
            "title": meta.get("title"),
            "url": url,
            "division": None,
            "sha256": meta.get("sha256") or md_file.stem,
            "page_count": max(pages) if pages else (1 if meta.get("kind") == "html" else None),
        })
    return records
