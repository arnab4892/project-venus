"""Chunk the active release's documents for embedding (LLD-RET-01/02/04).

Chunking runs over the converted Markdown of the **active release's** documents only
(``facts.active_document`` ⋈ ``data/<client>/md/<sha256>.md``). It produces
:class:`Chunk` records — heading-delimited, 400–600 tokens with a 60-token overlap,
tables kept whole (or split by rows with the header repeated when they would exceed the
embed limit) — each tagged with the families it talks about.

Three RET-04 behaviours live here:

* **Per-document disposition** ``chunk | reference_only | excluded`` — seeded by junk
  heuristics (substantive word count, link-to-text ratio, cross-document paragraph
  duplication), written to a human-editable ``clients/<client>/seeds/chunking.yaml`` and
  approved at Gate 1. ``reference_only`` keeps the document + its facts but yields no
  chunks (e.g. a catalogue link-hub page).
* **Corpus-wide paragraph dedup** — a normalised paragraph appearing in several documents
  is chunked once from a canonical source (PDF preferred) and skipped elsewhere.
* **Mechanical family tagging** — ``family_ids`` come from the promoted facts rows whose
  section (``source_doc_id`` + heading) the chunk covers, plus frozen family-name matches.
  Never guessed by an LLM.

Token counting + the loud :class:`ChunkTooLargeError` (never silent truncation) come from
:mod:`agentkit.retrieval.tokenizer`. Framework-generic — no client strings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy import Connection, text

from agentkit.extract.markdown import Section, read_document_sections
from agentkit.ingest.finish import output_path
from agentkit.retrieval.tokenizer import ChunkTooLargeError, TokenCounter

_REPO_ROOT = Path(__file__).resolve().parents[3]


def chunking_yaml_path(client: str) -> Path:
    """Location of the human-editable disposition config (LLD-RET-04)."""
    return _REPO_ROOT / "clients" / client / "seeds" / "chunking.yaml"

# Disposition values (LLD-RET-04).
CHUNK = "chunk"
REFERENCE_ONLY = "reference_only"
EXCLUDED = "excluded"
DISPOSITIONS = (CHUNK, REFERENCE_ONLY, EXCLUDED)

# Chunk sizing (LLD-RET-01).
TARGET_MIN_TOKENS = 400
TARGET_MAX_TOKENS = 600
OVERLAP_TOKENS = 60

# Disposition heuristic thresholds (LLD-RET-04). Deliberately conservative — the human
# edits chunking.yaml at Gate 1; these only seed the first pass.
_MIN_SUBSTANTIVE_WORDS = 40  # below this, a page has no chunkable prose
_LINK_HUB_MIN_LINKS = 8  # this many links + little prose → reference_only (link hub)
_DEDUP_MIN_WORDS = 15  # only dedup paragraphs at least this long (skip shared labels)

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_HEADING_LINE_RE = re.compile(r"^\s{0,3}#{1,6}\s")
_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass
class Chunk:
    """One embeddable slice of a document (mirrors ``facts.chunk``)."""

    chunk_id: str
    doc_id: str
    locator: str
    heading_path: list[str]
    content_md: str
    token_count: int
    family_ids: list[str]
    division: str | None

    def embedding_text(self) -> str:
        """The text fed to the embedder = heading path + content (context for retrieval)."""
        return "\n".join([*self.heading_path, self.content_md])


@dataclass
class DocDisposition:
    """Per-document disposition + the heuristic evidence behind the seeded value."""

    doc_id: str
    sha256: str
    kind: str
    disposition: str
    reason: str
    substantive_words: int
    link_ratio: float


@dataclass
class FamilyRefs:
    """Mechanical family lookup built from the active facts (never an LLM)."""

    by_section: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)  # family_id -> name
    division: dict[str, str] = field(default_factory=dict)  # family_id -> division


@dataclass
class ChunkReport:
    """What Gate 1 shows the human before any embedding."""

    dispositions: list[DocDisposition]
    chunk_counts: dict[str, int]  # doc_id -> chunk count
    dedup: list[dict]  # {"paragraph": preview, "canonical": doc_id, "dropped_from": [...]}
    largest: dict | None  # {"chunk_id","locator","token_count"} of the biggest chunk

    @property
    def total_chunks(self) -> int:
        return sum(self.chunk_counts.values())


# ---------------------------------------------------------------------------
# Text → blocks (paragraphs and tables)
# ---------------------------------------------------------------------------


def _norm_heading(heading: str) -> str:
    return re.sub(r"\s+", " ", heading).strip().upper()


def _section_heading_key(section: Section) -> str:
    """The heading string that a facts ``source_locator`` (post-``§``) matches against."""
    return " › ".join(section.heading_path) if section.heading_path else "(preamble)"


def _locator_heading(locator: str) -> str:
    """Extract the heading from a facts ``source_locator`` (drops any ``pN`` page prefix)."""
    _, _, heading = locator.rpartition("§")
    return heading.strip() or locator.strip()


def _clean_line(line: str) -> str:
    return _HTML_COMMENT_RE.sub("", line).rstrip()


def _is_table_line(line: str) -> bool:
    return bool(_TABLE_LINE_RE.match(line))


@dataclass
class _Block:
    text: str
    is_table: bool


def split_blocks(section_text: str) -> list[_Block]:
    """Split a section's body into paragraph and table blocks (blank-line delimited).

    Consecutive ``| … |`` lines are grouped as one table block; HTML comment markers
    (``<!-- page N -->``, ``<!-- image -->``) are dropped.
    """
    blocks: list[_Block] = []
    buf: list[str] = []
    buf_table = False

    def flush() -> None:
        nonlocal buf, buf_table
        body = "\n".join(buf).strip()
        if body:
            blocks.append(_Block(text=body, is_table=buf_table))
        buf = []
        buf_table = False

    for raw in section_text.splitlines():
        line = _clean_line(raw)
        if not line.strip():
            flush()
            continue
        is_tbl = _is_table_line(line)
        if buf and is_tbl != buf_table:
            # Transition between prose and table without a blank line.
            flush()
        buf_table = is_tbl
        buf.append(line)
    flush()
    return blocks


def split_table_by_rows(
    table_md: str, *, budget: int, count_tokens: TokenCounter, locator: str, embed_limit: int
) -> list[str]:
    """Split a markdown table into row-groups, repeating the header in each part.

    A group grows until adding the next body row would exceed ``budget`` tokens. A single
    body row that with the header already reaches ``embed_limit`` raises
    :class:`ChunkTooLargeError` (a table row is the atomic unit — it cannot be split).
    """
    lines = [ln for ln in table_md.splitlines() if ln.strip()]
    if not lines:
        return []
    header: list[str] = [lines[0]]
    body_start = 1
    if len(lines) > 1 and _TABLE_SEP_RE.match(lines[1]):
        header.append(lines[1])
        body_start = 2
    body_rows = lines[body_start:]
    if not body_rows:  # header-only table
        return ["\n".join(header)]

    header_md = "\n".join(header)
    parts: list[str] = []
    group: list[str] = []
    for row in body_rows:
        candidate = "\n".join([header_md, *group, row])
        if count_tokens(candidate) >= embed_limit and not group:
            raise ChunkTooLargeError(
                locator=locator, token_count=count_tokens(candidate), embed_limit=embed_limit
            )
        if group and count_tokens(candidate) > budget:
            parts.append("\n".join([header_md, *group]))
            group = [row]
        else:
            group.append(row)
    if group:
        parts.append("\n".join([header_md, *group]))
    return parts


# ---------------------------------------------------------------------------
# Chunking one document's sections
# ---------------------------------------------------------------------------


def _tail_overlap(text: str, overlap_tokens: int, count_tokens: TokenCounter) -> str:
    """Return the trailing ~``overlap_tokens`` of ``text`` (word-granular)."""
    if overlap_tokens <= 0:
        return ""
    words = text.split()
    if not words:
        return ""
    tail: list[str] = []
    for word in reversed(words):
        tail.insert(0, word)
        if count_tokens(" ".join(tail)) >= overlap_tokens:
            break
    return " ".join(tail)


def _slug(doc_id: str) -> str:
    return doc_id[4:] if doc_id.startswith("doc.") else doc_id


def chunk_sections(
    doc_id: str,
    sections: list[Section],
    *,
    count_tokens: TokenCounter,
    embed_limit: int,
    family_refs: FamilyRefs | None = None,
    keep_paragraph=None,
    division: str | None = None,
    target_max: int = TARGET_MAX_TOKENS,
    overlap: int = OVERLAP_TOKENS,
) -> list[Chunk]:
    """Chunk one document's sections into :class:`Chunk` records.

    Blocks are packed greedily to ``target_max`` tokens; oversized tables are split by
    rows (header repeated); consecutive chunks of one section share an ``overlap``-token
    tail. Any finished chunk at/over ``embed_limit`` raises :class:`ChunkTooLargeError`.
    ``keep_paragraph(norm_text) -> bool`` (corpus dedup) drops non-canonical duplicate
    paragraphs. ``family_ids``/``division`` are attached mechanically.
    """
    family_refs = family_refs or FamilyRefs()
    chunks: list[Chunk] = []
    counter = 0

    for section in sections:
        heading_key = _section_heading_key(section)
        # Expand blocks: dedup-drop prose duplicates, pre-split oversized tables.
        units: list[str] = []
        for block in split_blocks(section.text):
            if block.is_table and count_tokens(block.text) > target_max:
                units.extend(
                    split_table_by_rows(
                        block.text,
                        budget=target_max,
                        count_tokens=count_tokens,
                        locator=section.locator,
                        embed_limit=embed_limit,
                    )
                )
                continue
            if not block.is_table and keep_paragraph is not None:
                if not keep_paragraph(_normalise_paragraph(block.text)):
                    continue
            units.append(block.text)
        if not units:
            continue

        # Greedy pack units into windows of ~target_max tokens.
        windows: list[str] = []
        cur: list[str] = []
        for unit in units:
            candidate = "\n\n".join([*cur, unit])
            if cur and count_tokens(candidate) > target_max:
                windows.append("\n\n".join(cur))
                cur = [unit]
            else:
                cur.append(unit)
        if cur:
            windows.append("\n\n".join(cur))

        prev_tail = ""
        for window in windows:
            content = f"{prev_tail}\n\n{window}".strip() if prev_tail else window
            fam_ids = _tag_families(content, heading_key, doc_id, family_refs)
            chunk = Chunk(
                chunk_id=f"ch.{_slug(doc_id)}.{counter:04d}",
                doc_id=doc_id,
                locator=section.locator,
                heading_path=list(section.heading_path),
                content_md=content,
                token_count=0,
                family_ids=fam_ids,
                division=_chunk_division(fam_ids, family_refs) or division,
            )
            tc = count_tokens(chunk.embedding_text())
            if tc >= embed_limit:
                raise ChunkTooLargeError(
                    locator=section.locator, token_count=tc, embed_limit=embed_limit
                )
            chunk.token_count = tc
            chunks.append(chunk)
            counter += 1
            prev_tail = _tail_overlap(window, overlap, count_tokens)

    return chunks


# ---------------------------------------------------------------------------
# Mechanical family tagging + division
# ---------------------------------------------------------------------------


def _tag_families(content: str, heading_key: str, doc_id: str, refs: FamilyRefs) -> list[str]:
    """family_ids = facts rows covering this section ∪ frozen family-name matches."""
    found: set[str] = set(refs.by_section.get((doc_id, _norm_heading(heading_key)), set()))
    haystack = content.lower()
    for family_id, name in refs.names.items():
        if name and name.lower() in haystack:
            found.add(family_id)
    return sorted(found)


def _chunk_division(family_ids: list[str], refs: FamilyRefs) -> str | None:
    """The division shared by a chunk's families (majority; ``None`` if none/ambiguous)."""
    divs = [refs.division[f] for f in family_ids if f in refs.division]
    if not divs:
        return None
    # Most common division among the chunk's families.
    return max(set(divs), key=divs.count)


def build_family_refs(conn: Connection, release_id: str) -> FamilyRefs:
    """Build the mechanical family lookup from one release's facts (never an LLM)."""
    refs = FamilyRefs()
    section_sql = """
        SELECT family_id, source_doc_id, source_locator FROM facts.product_family
          WHERE release_id = :rid
        UNION ALL
        SELECT family_id, source_doc_id, source_locator FROM facts.capability_row
          WHERE release_id = :rid
        UNION ALL
        SELECT family_id, source_doc_id, source_locator FROM facts.product
          WHERE release_id = :rid
    """
    for row in conn.execute(text(section_sql), {"rid": release_id}).mappings():
        if not row["family_id"] or not row["source_doc_id"] or not row["source_locator"]:
            continue
        key = (row["source_doc_id"], _norm_heading(_locator_heading(row["source_locator"])))
        refs.by_section.setdefault(key, set()).add(row["family_id"])
    for row in conn.execute(
        text("SELECT family_id, name, division FROM facts.product_family WHERE release_id = :rid"),
        {"rid": release_id},
    ).mappings():
        refs.names[row["family_id"]] = row["name"]
        if row["division"]:
            refs.division[row["family_id"]] = row["division"]
    return refs


# ---------------------------------------------------------------------------
# Disposition heuristics + corpus dedup
# ---------------------------------------------------------------------------


def _normalise_paragraph(text_block: str) -> str:
    return re.sub(r"\s+", " ", text_block).strip().lower()


def _substantive_words(sections: list[Section]) -> int:
    """Word count across section bodies, excluding headings, link stubs and tables."""
    words = 0
    for section in sections:
        for block in split_blocks(section.text):
            if block.is_table:
                continue
            stripped = _MD_LINK_RE.sub(" ", block.text)
            words += len([w for w in stripped.split() if any(c.isalnum() for c in w)])
    return words


def _link_ratio(sections: list[Section]) -> float:
    """Fraction of body text that sits inside markdown links (a link-hub signal)."""
    body = "\n".join(s.text for s in sections)
    if not body.strip():
        return 0.0
    link_chars = sum(len(m.group(1)) for m in _MD_LINK_RE.finditer(body))
    total = len(re.sub(r"\s+", "", body)) or 1
    return round(link_chars / total, 3)


def _link_count(sections: list[Section]) -> int:
    body = "\n".join(s.text for s in sections)
    return len(_MD_LINK_RE.findall(body))


def seed_disposition(
    doc_id: str, sha256: str, kind: str, sections: list[Section]
) -> DocDisposition:
    """Seed a disposition from junk heuristics (LLD-RET-04); the human edits it at Gate 1.

    A page with little chunkable prose is either a **link hub** (many links → keep the
    document + its facts, produce no chunks) or a **stub** (nav / download page → excluded).
    Otherwise it is chunked.
    """
    words = _substantive_words(sections)
    ratio = _link_ratio(sections)
    links = _link_count(sections)
    if words < _MIN_SUBSTANTIVE_WORDS and links >= _LINK_HUB_MIN_LINKS:
        disp = REFERENCE_ONLY
        reason = f"link hub: {links} links, {words} prose words (link-ratio {ratio})"
    elif words < _MIN_SUBSTANTIVE_WORDS:
        disp = EXCLUDED
        reason = f"stub: {words} prose words (< {_MIN_SUBSTANTIVE_WORDS}), {links} links"
    else:
        disp = CHUNK
        reason = f"content: {words} prose words, {links} links (link-ratio {ratio})"
    return DocDisposition(
        doc_id=doc_id,
        sha256=sha256,
        kind=kind,
        disposition=disp,
        reason=reason,
        substantive_words=words,
        link_ratio=ratio,
    )


def _canonical_rank(doc_id: str, kind: str) -> tuple:
    """Sort key for choosing the canonical owner of a shared paragraph (PDF wins)."""
    return (0 if kind == "pdf" else 1, doc_id)


def build_dedup_index(
    sections_by_doc: dict[str, list[Section]], kinds: dict[str, str]
) -> tuple[dict[str, str], list[dict]]:
    """Map each cross-document duplicate paragraph → its canonical doc_id.

    Returns ``(canonical_by_paragraph, report_rows)``. Only paragraphs of at least
    :data:`_DEDUP_MIN_WORDS` words that appear in more than one document are deduped.
    """
    doc_paragraphs: dict[str, set[str]] = {}
    for doc_id, sections in sections_by_doc.items():
        seen: set[str] = set()
        for section in sections:
            for block in split_blocks(section.text):
                if block.is_table:
                    continue
                if len(block.text.split()) < _DEDUP_MIN_WORDS:
                    continue
                seen.add(_normalise_paragraph(block.text))
        doc_paragraphs[doc_id] = seen

    para_docs: dict[str, list[str]] = {}
    for doc_id, paras in doc_paragraphs.items():
        for para in paras:
            para_docs.setdefault(para, []).append(doc_id)

    canonical: dict[str, str] = {}
    report: list[dict] = []
    for para, docs in para_docs.items():
        if len(docs) < 2:
            continue
        winner = min(docs, key=lambda d: _canonical_rank(d, kinds.get(d, "html")))
        canonical[para] = winner
        report.append(
            {
                "paragraph": para[:80] + ("…" if len(para) > 80 else ""),
                "canonical": winner,
                "dropped_from": sorted(d for d in docs if d != winner),
            }
        )
    return canonical, report


# ---------------------------------------------------------------------------
# chunking.yaml (human-editable disposition config)
# ---------------------------------------------------------------------------


def read_dispositions_config(client: str) -> dict[str, str]:
    """Read approved per-document dispositions from ``chunking.yaml`` (empty if absent)."""
    path = chunking_yaml_path(client)
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[str, str] = {}
    for doc_id, spec in (data.get("documents") or {}).items():
        disp = spec.get("disposition") if isinstance(spec, dict) else spec
        if disp not in DISPOSITIONS:
            raise ValueError(f"chunking.yaml: {doc_id!r} has invalid disposition {disp!r}")
        out[doc_id] = disp
    return out


def write_dispositions_config(client: str, dispositions: list[DocDisposition]) -> Path:
    """Write the seeded/approved dispositions to ``chunking.yaml`` (human edits, re-runs)."""
    path = chunking_yaml_path(client)
    lines = [
        "# Per-document chunking disposition (LLD-RET-04). Edit `disposition` then re-run",
        "# `agentkit chunk run` / `agentkit release embed`.",
        "#   chunk          — split into embeddable chunks",
        "#   reference_only — keep the document + its facts, produce NO chunks (e.g. link hub)",
        "#   excluded       — drop entirely (nav / download stub)",
        "documents:",
    ]
    for d in sorted(dispositions, key=lambda x: x.doc_id):
        lines.append(f"  {d.doc_id}:")
        lines.append(f"    disposition: {d.disposition}")
        lines.append(f"    reason: {yaml.safe_dump(d.reason).strip()}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Corpus orchestration
# ---------------------------------------------------------------------------


def resolve_active_release(conn: Connection) -> str:
    """Return the single active release id (raises if none is active)."""
    rid = conn.execute(
        text("SELECT release_id FROM facts.release WHERE is_active")
    ).scalar_one_or_none()
    if rid is None:
        raise ValueError("no active release — promote + activate one first")
    return rid


def release_documents(conn: Connection, release_id: str) -> list[dict]:
    """Documents of one release with the fields chunking needs."""
    rows = conn.execute(
        text(
            "SELECT doc_id, kind, division, sha256 FROM facts.document "
            "WHERE release_id = :rid ORDER BY doc_id"
        ),
        {"rid": release_id},
    ).mappings()
    return [dict(r) for r in rows]


def _sections_for(client: str, sha256: str, data_root: Path | None) -> list[Section]:
    md_path = output_path(client, sha256, data_root=data_root)
    if not md_path.exists():
        raise FileNotFoundError(f"converted markdown missing for sha256 {sha256}: {md_path}")
    return read_document_sections(md_path)


def chunk_corpus(
    conn: Connection,
    client: str,
    release_id: str,
    *,
    count_tokens: TokenCounter,
    embed_limit: int,
    data_root: Path | None = None,
) -> tuple[list[Chunk], ChunkReport]:
    """Chunk every ``chunk``-disposition document of a release, applying approved overrides.

    Reads sections from the release's documents' converted Markdown (joined by sha256), seeds
    dispositions from heuristics, overlays any approved ``chunking.yaml``, dedupes shared
    paragraphs across the chunkable set, and tags families mechanically. Returns the chunks
    plus the :class:`ChunkReport` shown at Gate 1. Release-scoped so it also serves a
    freshly-promoted, not-yet-active release (LLD-REL-05).
    """
    docs = release_documents(conn, release_id)
    sections_by_doc = {d["doc_id"]: _sections_for(client, d["sha256"], data_root) for d in docs}
    kinds = {d["doc_id"]: (d["kind"] or "html") for d in docs}

    overrides = read_dispositions_config(client)
    dispositions: list[DocDisposition] = []
    for d in docs:
        seeded = seed_disposition(
            d["doc_id"], d["sha256"], kinds[d["doc_id"]], sections_by_doc[d["doc_id"]]
        )
        if d["doc_id"] in overrides and overrides[d["doc_id"]] != seeded.disposition:
            seeded.disposition = overrides[d["doc_id"]]
            seeded.reason = f"approved override → {seeded.disposition} ({seeded.reason})"
        dispositions.append(seeded)

    chunkable = {d.doc_id for d in dispositions if d.disposition == CHUNK}
    canonical, dedup_report = build_dedup_index(
        {doc_id: sections_by_doc[doc_id] for doc_id in chunkable}, kinds
    )
    family_refs = build_family_refs(conn, release_id)

    chunks: list[Chunk] = []
    chunk_counts: dict[str, int] = {}
    for d in docs:
        if d["doc_id"] not in chunkable:
            chunk_counts[d["doc_id"]] = 0
            continue
        doc_id = d["doc_id"]

        def keep(norm_para: str, _doc_id: str = doc_id) -> bool:
            return canonical.get(norm_para, _doc_id) == _doc_id

        doc_chunks = chunk_sections(
            doc_id,
            sections_by_doc[doc_id],
            count_tokens=count_tokens,
            embed_limit=embed_limit,
            family_refs=family_refs,
            keep_paragraph=keep,
            division=d["division"],
        )
        chunks.extend(doc_chunks)
        chunk_counts[doc_id] = len(doc_chunks)

    largest = None
    if chunks:
        big = max(chunks, key=lambda c: c.token_count)
        largest = {
            "chunk_id": big.chunk_id,
            "locator": f"{big.doc_id} {big.locator}",
            "token_count": big.token_count,
        }
    report = ChunkReport(
        dispositions=dispositions,
        chunk_counts=chunk_counts,
        dedup=dedup_report,
        largest=largest,
    )
    return chunks, report


def format_report(report: ChunkReport, *, embed_limit: int) -> str:
    """Human-readable Gate-1 disposition report."""
    lines = ["Chunking disposition report (Gate 1 — approve before embedding)", ""]
    lines.append(f"{'document':44} {'disp':14} {'chunks':>6}  reason")
    for d in sorted(report.dispositions, key=lambda x: x.doc_id):
        n = report.chunk_counts.get(d.doc_id, 0)
        lines.append(f"{d.doc_id:44} {d.disposition:14} {n:>6}  {d.reason}")
    lines.append("")
    by_disp: dict[str, int] = {}
    for d in report.dispositions:
        by_disp[d.disposition] = by_disp.get(d.disposition, 0) + 1
    disp_summary = " ".join(f"{k}={v}" for k, v in sorted(by_disp.items()))
    lines.append(f"documents: {len(report.dispositions)} ({disp_summary})")
    lines.append(f"total chunks: {report.total_chunks}")
    if report.largest:
        lines.append(
            f"largest chunk: {report.largest['chunk_id']} "
            f"({report.largest['token_count']} tokens, limit {embed_limit}) "
            f"@ {report.largest['locator']}"
        )
    if report.dedup:
        lines.append(f"deduped paragraphs (chunked once, dropped elsewhere): {len(report.dedup)}")
        for row in report.dedup[:20]:
            lines.append(
                f"  canonical={row['canonical']} dropped_from={','.join(row['dropped_from'])}"
                f"  “{row['paragraph']}”"
            )
        if len(report.dedup) > 20:
            lines.append(f"  … and {len(report.dedup) - 20} more")
    return "\n".join(lines)
