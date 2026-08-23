---
title: Milestone 2 — ingestion stage 1 (crawl & convert)
date: 2026-08-23
author: Claude Code (paired with arnab.sharma)
type: implementation note (not a CR — no PRD change)
lld_items: [LLD-ING-01, LLD-ING-02, LLD-ING-03, LLD-ING-04, LLD-ING-05]
prd_row: PRD-F-014
branch: milestone-2-ingestion-stage-1
---

# Milestone 2 — ingestion stage 1 (crawl & convert)

Second milestone in the CLAUDE.md build order: crawl the public website + the two
catalogue PDFs and convert them to review-ready Markdown under
`data/jyotech/md/`, recording one `staging.document` row per source. **No
extraction, no LLM, no chunking/embedding.** The output is what a human reviews
against the original PDFs before any downstream extraction is built. Nothing is
written to `facts.*` (verified: `facts.document` stays at the seed's 6 rows).

## 1. What was implemented

### Discovery → `clients/jyotech/seeds/sources.yaml` (LLD-ING-01)
- A one-time live depth-2 crawl from `index.php` (same-host, follows
  `.html/.php/.pdf`, 1 s delay) discovered **27 unique HTML/PHP pages + 2 PDFs**.
- `strip_selectors` were derived by inspecting the real Bootstrap markup and
  verified against four pages to remove all chrome (nav, header, dark footer band,
  top contact/social bar, copyright bar, floating social/WhatsApp, back-to-top,
  maps + `clients.html` iframes, scripts/styles/modals) while preserving every
  heading and content table.
- The two catalogue PDFs really contain a literal space (and `&` in the F&S name)
  on the live site — stored **percent-encoded** (`%20`, `%26`). The raw-space and
  `%20` spellings of two pages collapse to one canonical form each.
- The file carries an explicit **`exclude: []`** so pruning is a visible, human
  config decision (see §2 on the report's candidate flags).

### Pipeline — `src/agentkit/ingest/` (client-agnostic; no Jyotech strings)
- `sources.py` — loads `sources.yaml`; `bootstrap_rc_id()`; deterministic
  `doc_id_for(url)` (`…/about.html → doc.about`, stable across `%20`/space forms).
- `crawl.py` (LLD-ING-01/04) — httpx fetch with custom UA, **SHA-256 of raw
  bytes**; a failed fetch is recorded, not fatal.
- `html_convert.py` (LLD-ING-02) — BeautifulSoup strip via the selector list, then
  structural Markdown: headings, paragraphs, **HTML tables → pipe tables**, image
  alt text, **preserved (absolutised) PDF links**; HTML comments skipped (see §2).
- `pdf_convert.py` (LLD-ING-03) — Docling with table structure **on**; renders
  per page; pages with **< 50 chars/page** get an OCR pass (only those pages use
  the OCR output); `<!-- page N -->` markers between pages.
- `finish.py` (LLD-ING-04) — **NFC-only** Unicode cleanup (never NFKC — see §2),
  zero-width/NBSP normalisation, five-field front-matter `{url, kind, sha256,
  fetched_at, title}`, output at `data/<client>/md/<sha256>.md`.
- `patches.py` — deterministic per-document hook: apply
  `clients/<client>/patches/<sha256>.patch` (unified diff) after conversion;
  directory empty for now (`.gitkeep`).
- `staging_write.py` (LLD-ING-05) — upsert one **`staging.document`** row per
  source keyed by `(release_candidate_id, doc_id)`; runs on the caller's
  connection, never commits (mirrors `release/seed.py`).
- `pipeline.py` — orchestrates fetch → hash-skip → convert → patch → finish →
  write → staging, and builds the **conversion report**.

### CLI — `src/agentkit/cli.py`
- `agentkit ingest run [CLIENT] [--rc rc.<client>.bootstrap]` — runs the pipeline,
  wraps staging writes in one commit, prints the conversion report.

### Supporting
- Deps: added `beautifulsoup4` + `lxml` to `pyproject.toml`.
- Tests (test-first, `tests/ingest/`, **network + Docling mocked**, 28 tests):
  `test_html_convert` (strip incl. comments, pipe table, preserved PDF link, image
  alt, heading/table counts, title, Nm³/hr intact), `test_pdf_convert` (page
  markers, the < 50 char/page OCR decision, OCR invoked only when a low-density
  page exists, table count), `test_finish` (Nm³/hr survives, NBSP/zero-width
  cleanup, front-matter), `test_pipeline` (first run writes both / **second run
  writes nothing** / front-matter + intact Nm³/hr / unreachable recorded),
  `test_sources`, `test_report_flags`, `test_patches`.

### Live run result (clean slate)
`agentkit ingest run jyotech`: **written 28, skipped 1, unreachable 0; 156
headings, 6 tables, 4 pages OCR'd** (PROCESS p14/15/17, F&S p23). Both catalogues
converted (PROCESS 18 pp, F&S multi-page). Re-run → **written 0, skipped 29**
(idempotent, no Docling re-run). `staging.document` holds 29 rows (27 html + 2
pdf); `facts.document` unchanged at 6. `pytest -q` → 38 passed.

## 2. Deviations & notes

- **"11 HTML URLs" (LLD-ING-01) vs 27 crawled — reconciled, not a conflict.** The
  homepage **primary nav has exactly 11 page links** — that is the LLD's count.
  The other 16 are legitimate second-level pages reached at depth 2. "11" was a
  design-time count, not a limit. Per the user's decision we keep all 27; the
  LLD-ING-01 wording will be folded in during the next doc pass. The two
  suspicious filenames are real nav pages (`gallery-test2.html` = "Gallery",
  `catalogue-testing.html` = "Catalogue") — poor developer naming, verified
  against the homepage nav.
- **Curation is content-based and human-owned.** `sources.yaml` has an explicit
  `exclude: []`; the report flags **candidates** only (never auto-excludes):
  near-duplicate text (Jaccard ≥ 0.9), near-empty pages, and nav-orphaned pages
  (off the 11-item primary nav). The live run flagged the 16 depth-2 pages plus
  `catalogue1.html`/`career.php` as nav-orphaned — expected; junk that stays does
  little harm since the section classifier later routes non-product content to
  `other` and out of the fact store.
- **`catalogue1.html` and `catalogue-testing.html` are byte-identical** (same
  SHA-256). Both link the same two PDFs. The hash-skip collapses them to one
  `<sha>.md`; **both `doc_id`s still get a `staging.document` row** (provenance for
  each URL preserved). This is why the run shows `skipped 1` on a clean slate.
- **RC id.** Stage-1 staging writes use a per-client bootstrap id
  `rc.jyotech.bootstrap` (optional `--rc` override), derived generically from
  `BOOTSTRAP_RC_ID_TEMPLATE`. **RC lifecycle (create/list/status) is deliberately
  deferred to milestone 3**, which will pass an explicit `--rc`.
- **Idempotency is keyed on source bytes** (`<sha>.md` existence), not on the
  converter version (LLD-ING-04). A converter change therefore requires a clean
  rebuild (`rm -rf data/<client>/md` before re-running) — otherwise unchanged
  sources are skipped. Noted for the extraction milestone.
- **`Nm³/hr` — the live site uses ASCII `Nm3/hr` everywhere.** The superscript
  form does not appear on the live pages, and Docling's PDF text layer also emits
  `Nm3/hr`. The "must survive intact" guarantee is exercised by the unit-test
  fixture (which injects `&#179;`) and enforced by **NFC-only** cleanup (NFKC would
  fold ³→3). Both forms are preserved as-authored; nothing is normalised across
  them here (unit canonicalisation lives in `extract/normalise.py`, LLD-EXT-09).
- **HTML comments were leaking as text** (bs4's `Comment` is a `NavigableString`
  subclass): the first live run showed `<!-- Spinner Start -->` etc. as content.
  Fixed by skipping `Comment` nodes in the walker + inline renderer, with a
  fixture assertion; the final output is comment-free.
- **Docling Markdown escaping disabled** (`escape_html=False`,
  `escape_underscores=False`) so catalogue text reads cleanly (`AIR & GAS`, not
  `AIR &amp; GAS`) for the human review.
- **`staging.document` only.** `division` and `title` are left for the extraction
  milestone (`title` currently comes from `<title>`, which on this site is the
  generic "JYOTECH" for most pages).
- **The shipped crawler is driven by `sources.yaml`.** The depth-2 link discovery
  that produced the file was a one-time bootstrap (throwaway script); a future
  `agentkit ingest discover` could regenerate `sources.yaml`. LLD-ING-01's
  "same-host crawl, depth 2" describes how the seed list was built.
- **PDF tests mock Docling** (decision below): the wrapper is unit-tested against a
  stubbed page-render seam, so tests are deterministic and never download models.

## 3. Ambiguities raised and how they were resolved

Three decisions were confirmed with the user before/while building.

### Q1 — Release-candidate id for stage-1 staging writes
`staging.document` needs a `release_candidate_id`, but RC tooling does not exist
yet. **Resolved:** optional `--rc` defaulting to a per-client bootstrap id
`rc.jyotech.bootstrap` (named `BOOTSTRAP_RC_ID_TEMPLATE`); re-runs upsert under it.
RC lifecycle is deferred to milestone 3.

### Q2 — Docling in tests
Docling downloads models on first use (network + slow) and is non-deterministic —
at odds with "network mocked in tests". **Resolved:** mock Docling; unit-test our
wrapper (page markers, < 50 char/page OCR decision, front-matter) against a stubbed
render seam. Real Docling runs only via the CLI (which the user reviews anyway).

### Q3 — Page set: 27 crawled vs the LLD's 11
**Resolved:** keep all 27 in-scope pages; do **not** exclude by filename (the
test-looking names are real nav pages, verified against the homepage nav). Add an
explicit `exclude: []` so pruning is visible config, and have the report flag
exclusion candidates on content grounds (near-duplicate / near-empty /
nav-orphaned) for a human to decide. Note the LLD "11" correction here for the
doc pass.

## 4. Traceability
`PRD-F-014` row updated — Code: add `ingest/` (`ingest/{sources,crawl,html_convert,
pdf_convert,finish,patches,staging_write,pipeline,verify}.py`, `cli.py` ingest
sub-app); Tests: add `tests/ingest/` (html_convert, pdf_convert, finish, pipeline,
sources, report_flags, patches, verify, cleaner_rules).

---

# Milestone 2b — verified-clean conversion pass

A follow-up loop (fix → `ingest run` → `ingest verify` → repeat, 2 iterations)
took all sources to a verified-clean state. Added a permanent verifier, fixed
the PDF right-edge truncation and table extraction, added HTML cleaner rules, and
excluded one template page. `agentkit ingest verify` → **ALL PASS / 28 sources**;
`pytest -q` → 51 passed; ingest still idempotent (2nd run writes 0).

## 5. New capability — `agentkit ingest verify` (flagged design extension)
A new LLD-level command (`src/agentkit/ingest/verify.py`). **This extends the
design beyond LLD-ING** and should be folded into LLD-ING via `/prd-change` +
`/propagate-prd` in the next doc pass — recorded here, not silently added.
- **PDF sources:** an independent `pdftotext` (poppler) witness of the cached raw
  bytes yields a token multiset — numbers, units (`Nm3/hr|lpm|Bar|Barg|kW|HP`),
  standards (`API-618|ISO d+:d+|EN|NFPA`), models (`MCH-*|ICON|VEGA|NOVA|NEPTUNE|
  PROEYE`) — and every witness token (with multiplicity) must appear in the
  converted Markdown. Image-only content is invisible to `pdftotext` too, so the
  witness never demands tokens that live only in pictures — the check stays honest.
- **HTML sources:** assert none of the six banned junk patterns remain.
- Per-source PASS or the exact missing/mangled tokens; **non-zero exit** on any
  failure. Uses an `ingest-manifest.json` (url→sha) so byte-identical duplicate
  URLs both resolve to the one converted file, offline.
- **New dependency:** poppler's `pdftotext` (system binary; macOS `brew install
  poppler`). `ingest run` now also caches raw bytes to `data/<client>/raw/<sha>`
  and writes `data/<client>/ingest-manifest.json` (both under gitignored `data/`).

## 6. What was found and fixed
### PDF right-edge truncation (PROCESS p2: "PROCES", "Internatio", "45001:201", "fro")
**Diagnosis:** `pdftotext` had the full text → the loss was in Docling, not the
PDF layer. **Fix:** switch the Docling PDF backend to `PyPdfiumDocumentBackend`
(the default DoclingParse backend clips right-edge words). Now full:
"PROCESS ENGINEERING", "International", "45001:2018", "from its international".
No OCR or patch needed for this.

### PDF tables
- **PROCESS pp12–13** (H2 supply spec tables) now render as proper pipe tables.
  Two settings were both required with the pypdfium2 backend: `TableFormerMode.
  ACCURATE`, and `do_cell_matching = False` (with cell-matching on, pypdfium2
  merges a table's label column into the value cell; disabling it repopulates
  cells from the predicted grid). 3/3 PROCESS tables verified, columns intact.
- **F&S air-lifting-bags spec sheets (pp10–11) — WHAT REMAINS AND WHY.** These are
  graphical pages: the dimension grid is embedded in JPEG images (confirmed via
  `pdfimages`), with only scattered prose in the text layer. Docling detects **0**
  tables here under FAST / ACCURATE / OCR. They **cannot** be rendered as pipe
  tables without OCR-reconstruction of the image grid (out of fidelity scope). The
  numeric specs that ARE in the text layer survive as prose (verified by the token
  witness). Recorded as a source/tooling limitation for the milestone-3 review.

### F&S p7 heading merge → the one patch (last resort, flagged)
`pdftotext` witness: `LP & HP BA TRAILERS`; Docling merged it to `LP & HPBA
TRAILERS` (lost the space), dropping the `HP` token — the sole `ingest verify`
failure on F&S. Docling config and OCR did not fix it (OCR was worse). Per the
escalation ladder (config → OCR → patch) a spacing patch restores the exact
printed text:
- **`clients/jyotech/patches/381fd655…4e3f.patch`** — `LP & HPBA TRAILERS` →
  `LP & HP BA TRAILERS` on F&S page 7. Reason: Docling text-join dropped the space
  between two adjacent tokens; unrecoverable via converter settings.
- Patches now apply **after** `tidy_markdown` (so a patch is authored against the
  same text the reviewer sees), and a patch that fails to apply is recorded as a
  non-fatal error rather than crashing the run.

### HTML cleaner rules (generic in code, per-client selectors/values in sources.yaml)
- **Breadcrumb lists** — strip `.breadcrumb`.
- **Trailing teaser-card sections** — strip `.blog-item`.
- **Bare `Image` alt nodes** — `drop_alt_text: [Image]`; the image is skipped.
- **`Read More` stubs** — `drop_link_text: [Read More]`; dropped wherever it forms
  a standalone node (it was an `<h5>` heading, not only an `<a>` — the generic rule
  now covers headings/paragraphs/anchors).
- **Consecutive `<!-- image -->`** collapsed to one, and **orphan single-character
  lines** dropped, in `tidy_markdown` (table rows preserved).

### Excludes
- Added `achievement.html` to `exclude:` — "template placeholder alt-text only, no
  real content".
- **Proposed additional excludes (NOT applied — your decision):**
  1. `fire-protection-clothing.html` — near-empty (6 non-blank lines; only headings
     + "Responsive Image" placeholders). Real theme-filler page.
  2. `catalogue1.html` OR `catalogue-testing.html` — byte-identical duplicates
     (same SHA-256); one is redundant. `catalogue-testing.html` is the live nav
     "Catalogue" page, so prefer excluding `catalogue1.html`.
  3. (refinement, not an exclude) add `"Responsive Image"` to `drop_alt_text` — it
     is another placeholder alt appearing across several pages; dropping it would
     tidy them (and make the near-empty page above even clearer).

## 7. Source conflict for milestone-3 (recorded, NOT resolved)
**Process-gas capacity, 20000 vs 25000 Nm3/hr.** The web page
`process-gas-compressors.html` states "PROCESS COMPRESSORS (RECIP.) … Capacity
range: Up to **20000** Nm3/hr". The PROCESS catalogue PDF states, for the same
"PROCESS COMPRESSORS (RECIP.)", "Capacity Range: Up to **25000** Nm3/hr" (its
Oxygen Compressors row is 20000). Both figures are preserved verbatim in their
respective converted outputs. Flagged for the extraction/review milestone to
adjudicate against the authoritative source — not resolved here.

## 8. Exit criteria status
- `agentkit ingest verify` PASS for all 28 sources ✔
- Six banned junk patterns in no HTML output (enforced by verify) ✔
- Source tables as pipe tables: **PROCESS ✔** (3/3, matching columns); **F&S ✗** —
  image-based spec sheets, not text-extractable (documented in §6, milestone-3).
- `pytest -q` green incl. verifier + cleaner-rule tests (51 passed) ✔
- Idempotent: immediate 2nd `ingest run` writes 0 ✔
