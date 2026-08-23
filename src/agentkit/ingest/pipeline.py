"""Ingestion stage-1 orchestrator (LLD-ING-01…05).

Per source: fetch → hash-skip → convert (HTML or PDF) → patch → finish → write
``data/<client>/md/<sha256>.md`` → upsert ``staging.document``. Accumulates a
conversion report (written/skipped, headings, tables, OCR'd pages, low-density
flags, unreachable URLs) plus content-based exclusion CANDIDATES (near-duplicate,
near-empty, nav-orphaned) — surfaced for a human, never auto-excluded.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import Connection

from .crawl import fetch
from .finish import manifest_path, output_path, raw_path, render_markdown, tidy_markdown
from .html_convert import html_to_markdown
from .patches import apply_patch_if_present
from .pdf_convert import pdf_to_markdown
from .sources import Sources, bootstrap_rc_id, doc_id_for, load_sources
from .staging_write import upsert_document

NEAR_EMPTY_CHARS = 200
NEAR_DUPLICATE_THRESHOLD = 0.9


# --------------------------------------------------------------------------- #
# Report types
# --------------------------------------------------------------------------- #
@dataclass
class Written:
    url: str
    kind: str
    sha256: str
    path: str
    doc_id: str
    headings: int = 0
    tables: int = 0
    pages_ocred: list[int] = field(default_factory=list)
    low_density_pages: list[int] = field(default_factory=list)
    patched: bool = False


@dataclass
class Skipped:
    url: str
    kind: str
    sha256: str
    path: str


@dataclass
class Failed:
    url: str
    error: str


@dataclass
class IngestReport:
    client: str
    rc_id: str
    written: list[Written] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    errors: list[Failed] = field(default_factory=list)
    near_duplicates: list[tuple[str, str]] = field(default_factory=list)
    near_empty: list[str] = field(default_factory=list)
    nav_orphaned: list[str] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        total_headings = sum(w.headings for w in self.written)
        total_tables = sum(w.tables for w in self.written)
        ocred = sum(len(w.pages_ocred) for w in self.written)
        low = [(w.url, w.low_density_pages) for w in self.written if w.low_density_pages]
        lines = [
            f"Ingestion report — client={self.client} rc={self.rc_id}",
            f"  written: {len(self.written)}   skipped (unchanged): {len(self.skipped)}"
            f"   unreachable: {len(self.errors)}",
            f"  headings found: {total_headings}   tables detected: {total_tables}"
            f"   pages OCR'd: {ocred}",
        ]
        if low:
            lines.append("  low-text-density pages (OCR'd):")
            lines += [f"    - {url}: pages {pages}" for url, pages in low]
        if self.errors:
            lines.append("  unreachable / 404 (recorded, not fatal):")
            lines += [f"    - {e.url}: {e.error}" for e in self.errors]
        # exclusion candidates (never auto-applied)
        if self.near_duplicates or self.near_empty or self.nav_orphaned:
            lines.append("  exclusion candidates (review — nothing auto-excluded):")
            for a, b in self.near_duplicates:
                lines.append(f"    - near-duplicate: {a}  <->  {b}")
            for u in self.near_empty:
                lines.append(f"    - near-empty: {u}")
            for u in self.nav_orphaned:
                lines.append(f"    - nav-orphaned: {u}")
        return lines


# --------------------------------------------------------------------------- #
# Content-based candidate flags (pure, unit-tested)
# --------------------------------------------------------------------------- #
def near_empty_pages(bodies: dict[str, str], *, min_chars: int = NEAR_EMPTY_CHARS) -> list[str]:
    return [url for url, text in bodies.items() if len(text.strip()) < min_chars]


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def near_duplicate_pairs(
    bodies: dict[str, str], *, threshold: float = NEAR_DUPLICATE_THRESHOLD
) -> list[tuple[str, str]]:
    items = list(bodies.items())
    toks = {url: _tokens(text) for url, text in items}
    pairs: list[tuple[str, str]] = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = toks[items[i][0]], toks[items[j][0]]
            if not a or not b:
                continue
            jaccard = len(a & b) / len(a | b)
            if jaccard >= threshold:
                pairs.append((items[i][0], items[j][0]))
    return pairs


def nav_orphans(pages: list[str], nav_urls: set[str]) -> list[str]:
    nav = set(nav_urls)
    return [p for p in pages if p not in nav]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _canonical(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, quote(unquote(parts.path)), parts.query, ""))


def _homepage_nav_urls(html: bytes, base_url: str) -> set[str]:
    """Canonical URLs linked from the homepage's primary nav (header/nav)."""
    soup = BeautifulSoup(html, "lxml")
    root = soup.find("header") or soup.find("nav")
    if root is None:
        return set()
    urls = set()
    for a in root.find_all("a", href=True):
        u = _canonical(urljoin(base_url, a["href"]))
        if urlsplit(u).path.lower().endswith((".html", ".php")):
            urls.add(u)
    return urls


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def run_ingest(
    client: str | None = None,
    *,
    sources: Sources | None = None,
    rc_id: str | None = None,
    http_client: httpx.Client | None = None,
    conn: Connection | None = None,
    data_root: Path | None = None,
    pdf_render=None,
) -> IngestReport:
    """Run stage-1 ingestion for ``client`` and return the conversion report."""
    src = sources or load_sources(client)
    client = src.client
    rc_id = rc_id or bootstrap_rc_id(client)
    delay = float(src.crawl.get("delay_seconds", 1) or 0)
    user_agent = src.crawl.get("user_agent")
    start_url_canon = _canonical(src.start_url)

    owns_client = http_client is None
    http = http_client or httpx.Client(follow_redirects=True, timeout=30.0)

    report = IngestReport(client=client, rc_id=rc_id)
    html_bodies: dict[str, str] = {}   # url -> markdown body (for dup/empty flags)
    nav_urls: set[str] = set()
    manifest: list[dict] = []          # url -> sha/paths (dup-safe witness map)

    def _manifest_entry(url: str, doc_id: str, sha: str, kind: str) -> None:
        manifest.append(
            {
                "url": url,
                "doc_id": doc_id,
                "sha256": sha,
                "kind": kind,
                "md_path": str(output_path(client, sha, data_root=data_root)),
                "raw_path": str(raw_path(client, sha, kind, data_root=data_root)),
            }
        )

    try:
        urls = src.active_pages() + src.active_pdfs()
        for idx, url in enumerate(urls):
            fetched = fetch(url, client=http, user_agent=user_agent)
            if not fetched.ok:
                report.errors.append(Failed(url=url, error=fetched.error or "unreachable"))
                continue

            # capture homepage nav on the way past
            if fetched.kind == "html" and _canonical(url) == start_url_canon:
                nav_urls = _homepage_nav_urls(fetched.content, src.start_url)

            sha = fetched.sha256
            out = output_path(client, sha, data_root=data_root)
            doc_id = doc_id_for(url)
            _manifest_entry(url, doc_id, sha, fetched.kind)

            if out.exists():  # unchanged bytes → already converted (LLD-ING-04)
                report.skipped.append(Skipped(url=url, kind=fetched.kind, sha256=sha, path=str(out)))
                rawp = raw_path(client, sha, fetched.kind, data_root=data_root)
                if not rawp.exists():  # keep the verifier's witness in sync
                    rawp.parent.mkdir(parents=True, exist_ok=True)
                    rawp.write_bytes(fetched.content)
                if conn is not None:
                    upsert_document(
                        conn, rc_id=rc_id, doc_id=doc_id, kind=fetched.kind,
                        url=url, sha256=sha, page_count=None,
                    )
                continue

            if fetched.kind == "pdf":
                pdf = pdf_to_markdown(
                    fetched.content, render=pdf_render
                ) if pdf_render else pdf_to_markdown(fetched.content)
                body, title = pdf.markdown, None
                headings, tables = 0, pdf.tables
                pages_ocred, low, page_count = pdf.pages_ocred, pdf.low_density_pages, pdf.page_count
            else:
                res = html_to_markdown(
                    fetched.content,
                    strip_selectors=src.strip_selectors,
                    base_url=url,
                    drop_alt_text=src.drop_alt_text,
                    drop_link_text=src.drop_link_text,
                )
                body, title = res.markdown, res.title
                headings, tables = res.headings, res.tables
                pages_ocred, low, page_count = [], [], None
                html_bodies[url] = body

            # tidy first, then apply any per-doc patch to the finished body so a
            # patch is authored against the same text the reviewer sees.
            body = tidy_markdown(body)
            try:
                body, patched = apply_patch_if_present(body, client, sha)
            except ValueError as exc:
                report.errors.append(Failed(url=url, error=f"patch {sha[:12]} failed: {exc}"))
                continue

            # cache raw source bytes for the offline verifier (LLD-ING verify)
            rawp = raw_path(client, sha, fetched.kind, data_root=data_root)
            rawp.parent.mkdir(parents=True, exist_ok=True)
            rawp.write_bytes(fetched.content)

            document = render_markdown(
                body,
                {
                    "url": url,
                    "kind": fetched.kind,
                    "sha256": sha,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "title": title,
                },
            )
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(document, encoding="utf-8")

            if conn is not None:
                upsert_document(
                    conn, rc_id=rc_id, doc_id=doc_id, kind=fetched.kind,
                    url=url, sha256=sha, page_count=page_count,
                )

            report.written.append(
                Written(
                    url=url, kind=fetched.kind, sha256=sha, path=str(out), doc_id=doc_id,
                    headings=headings, tables=tables, pages_ocred=pages_ocred,
                    low_density_pages=low, patched=patched,
                )
            )

            if delay and idx < len(urls) - 1:
                time.sleep(delay)
    finally:
        if owns_client:
            http.close()

    # write the url→sha manifest (dup-safe witness map for `ingest verify`)
    if manifest:
        mpath = manifest_path(client, data_root=data_root)
        mpath.parent.mkdir(parents=True, exist_ok=True)
        mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # content-based exclusion candidates (report only)
    report.near_empty = near_empty_pages(html_bodies)
    report.near_duplicates = near_duplicate_pairs(html_bodies)
    if nav_urls:
        report.nav_orphaned = nav_orphans(
            [u for u in src.active_pages() if _canonical(u) != start_url_canon], nav_urls
        )
    return report
