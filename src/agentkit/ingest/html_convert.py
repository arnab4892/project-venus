"""HTML → Markdown structural conversion (LLD-ING-02).

Strip client chrome via the CSS ``strip_selectors`` list, then convert the
remaining structure to Markdown: headings, paragraphs, tables as pipe tables,
image alt text, and preserved (absolutised) PDF links. Client-agnostic — the
selector list comes from the caller (``sources.yaml``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


@dataclass
class HtmlResult:
    markdown: str
    title: str | None
    headings: int
    tables: int


def html_to_markdown(
    html: bytes | str,
    *,
    strip_selectors: list[str],
    base_url: str,
    drop_alt_text: list[str] | None = None,
    drop_link_text: list[str] | None = None,
) -> HtmlResult:
    """Convert one HTML document to review-ready Markdown.

    ``strip_selectors`` remove client chrome (nav/footer/breadcrumb/teaser cards).
    The generic cleaner rules use per-client value lists from ``sources.yaml``:
    ``drop_alt_text`` (image alt placeholders like "Image" that become bare text
    nodes) and ``drop_link_text`` ("Read More" teaser stubs) — matched
    case-insensitively; the anchor/alt is dropped, its content lost by design.
    """
    soup = BeautifulSoup(html, "lxml")
    drop_alt = {t.strip().lower() for t in (drop_alt_text or [])}
    drop_link = {t.strip().lower() for t in (drop_link_text or [])}
    junk_text = drop_alt | drop_link  # dropped wherever it forms a standalone node

    title = soup.title.get_text(strip=True) if soup.title else None

    for sel in strip_selectors:
        for el in soup.select(sel):
            el.decompose()
    if soup.head:
        soup.head.decompose()

    body = soup.body or soup
    counters = {"headings": 0, "tables": 0}
    parts: list[str] = []

    def inline(node: Tag) -> str:
        """Render inline content, keeping PDF links and image alt text."""
        out: list[str] = []
        for child in node.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                out.append(str(child))
            elif child.name == "a":
                href = child.get("href", "") or ""
                text = child.get_text(" ", strip=True)
                if text.lower() in drop_link:
                    continue
                if ".pdf" in href.lower():
                    out.append(f"[{text}]({urljoin(base_url, href)})")
                else:
                    out.append(text)
            elif child.name == "img":
                alt = (child.get("alt") or "").strip()
                if alt and alt.lower() not in drop_alt:
                    out.append(alt)
            elif child.name == "br":
                out.append(" ")
            else:
                out.append(inline(child))
        text = "".join(out)
        text = re.sub(r"[ \t]*\n[ \t]*", " ", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()

    def table_md(table: Tag) -> str:
        rows = [
            [inline(c) for c in tr.find_all(["th", "td"], recursive=False) or tr.find_all(["th", "td"])]
            for tr in table.find_all("tr")
        ]
        rows = [r for r in rows if r]
        if not rows:
            return ""
        ncol = max(len(r) for r in rows)

        def fmt(r: list[str]) -> str:
            r = r + [""] * (ncol - len(r))
            return "| " + " | ".join(r) + " |"

        lines = [fmt(rows[0]), "| " + " | ".join(["---"] * ncol) + " |"]
        lines += [fmt(r) for r in rows[1:]]
        return "\n".join(lines)

    def list_md(lst: Tag, ordered: bool) -> str:
        items = []
        for i, li in enumerate(lst.find_all("li", recursive=False), start=1):
            marker = f"{i}." if ordered else "-"
            txt = inline(li)
            if txt:
                items.append(f"{marker} {txt}")
        return "\n".join(items)

    def walk(node: Tag) -> None:
        for child in node.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                text = str(child).strip()
                if text:
                    parts.append(text)
                continue
            name = child.name
            if name in _HEADINGS:
                txt = inline(child)
                if not txt or txt.lower() in junk_text:  # e.g. teaser "Read More" heading
                    continue
                counters["headings"] += 1
                level = int(name[1])
                parts.append("#" * level + " " + txt)
            elif name == "p":
                txt = inline(child)
                if txt and txt.lower() not in junk_text:
                    parts.append(txt)
            elif name == "table":
                counters["tables"] += 1
                md = table_md(child)
                if md:
                    parts.append(md)
            elif name in ("ul", "ol"):
                md = list_md(child, ordered=(name == "ol"))
                if md:
                    parts.append(md)
            elif name == "img":
                alt = (child.get("alt") or "").strip()
                if alt and alt.lower() not in drop_alt:
                    parts.append(alt)
            elif name == "a":
                if (child.get_text(" ", strip=True)).lower() in drop_link:
                    continue
                txt = inline(child)
                if txt:
                    parts.append(txt)
            elif name in ("br", "hr"):
                continue
            else:
                walk(child)

    walk(body)
    markdown = "\n\n".join(p for p in parts if p.strip() and p.strip().lower() not in junk_text)
    return HtmlResult(
        markdown=markdown,
        title=title,
        headings=counters["headings"],
        tables=counters["tables"],
    )
