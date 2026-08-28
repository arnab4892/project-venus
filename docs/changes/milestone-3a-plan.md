# Milestone 3a — Extraction to staging + review export

## Context

Milestones 1–2 built the data plane (schema, seed, `match_capability`) and stage-1
ingestion (crawl → review-ready Markdown under `data/jyotech/md/`, one
`staging.document` row per source under a bootstrap RC). Nothing has yet turned that
Markdown into structured, reviewable facts.

Milestone 3a is the extraction stage (LLD-EXT-01…10, HLD-C-02) plus the first half of
release tooling (LLD-REL-01): a minimal RC lifecycle, a two-pass LLM extractor that
lands **evidenced** rows in `staging.*` (never `facts.*`), and an xlsx export for human
review. It uses the external-capable extractor endpoint from CR-0002 over **public
content only**. Scope **ends at the exported spreadsheet**; import, integrity checks,
diff, and promote/activate are milestone 3b. There are **two human gates**: after
pass 1 (the proposed family list) and after export.

The intended outcome: `release create` → `extract run --rc` pauses at `families.yaml`
for approval → re-run completes both passes against the real corpus with the real
extraction model → `release export` produces a review xlsx, and we report families,
per-table row counts, evidence-dropped fields with examples, every `needs_family` and
`conflict_group` row, and token/cost totals.

## Decisions locked (confirmed with user)

1. **New staging fields as real columns** — `conflict_group text`, `needs_family bool`,
   `section_id text` added to the staging mirrors in a new `0002` migration. This is an
   **added implementation detail** that folds into LLD-DB-06 via the **clarification
   path (CLAUDE.md rule 6), NOT /prd-change** — no requirement or HLD decision changes.
   Recorded in the milestone note for the next doc pass; **LLD.md is not edited in this
   code milestone** (matches how milestone-2 deferred its `ingest verify` fold-in).
2. **Section splits, classifications, and per-LLM-call logs live as file artifacts**
   under `data/jyotech/extract/<rc>/` (`sections.json`, `classification.json`,
   `llm-calls.jsonl`) — consistent with ingest's `md/`, `raw/`, `ingest-manifest.json`.
   **Rider:** token/cost totals must be summarised into the printed run report **and**
   the milestone note, so the numbers don't live only in a gitignored file.
3. **`staging.release_candidate` is a standalone ledger, no FK** from the staging
   mirrors' `release_candidate_id`. Staging keeps its deliberate no-cross-table-FK
   stance; integrity is enforced at the release gate, not the waiting room. **Riders:**
   (a) add a code comment on the mirrors/migration noting the FK is intentionally absent
   and RC validity is enforced at the release gate; (b) record in the milestone note
   that **release promote (3b) must refuse any RC with no ledger row or whose status is
   not `exported`/`imported`** — that gate supersedes an FK because it validates status
   too. Bootstrap RC `rc.jyotech.bootstrap` stays valid for ingest without a ledger row.

## What already exists (reuse, don't rebuild)

- **CLI** `src/agentkit/cli.py` — Typer sub-apps; copy the `ingest run` pattern
  (`connect()` → `with conn.begin():` → business fn(conn), `typer.echo`). Add
  `release_app` and `extract_app` the same way.
- **Staging writer pattern** `src/agentkit/ingest/staging_write.py` — parameterised
  `INSERT … ON CONFLICT (release_candidate_id, …) DO UPDATE`, runs on caller's
  `Connection`, never commits. Mirror exactly for the six content tables + document.
- **Seven `staging.*` mirrors** already created in `migrations/versions/0001_init.py`
  with review columns (`evidence jsonb, confidence, review_status, reviewer,
  reviewed_at`) and `release_candidate_id` in the PK. Only new columns needed (decision 1).
- **`extract/normalise.py`** — unit/SCMD canonicalisation done; its docstring already
  flags the "up to X" range parsing as this milestone's extension.
- **`ingest/sources.py`** — `doc_id_for(url)`, `bootstrap_rc_id(client)`,
  `autodetect_client()`. Reuse `doc_id_for` for section provenance.
- **`ingest/finish.py`** — `output_path/raw_path/manifest_path`, front-matter shape
  (`url,kind,sha256,fetched_at,title`), page marker `<!-- page N -->`. There is **no md
  reader** — 3a adds one.
- **Test harness** `tests/conftest.py` — real Postgres; `seeded_conn` = transactional
  rollback; DB fixtures skip when Postgres is down. House style: inject a callable seam
  (like ingest's `render=`) rather than patching — use `complete=` for the LLM.
- **`openpyxl`** already a dependency (for export). **`openai` is NOT** — add it.
- **CR-0002** already specifies the `config.py` additions and the two named tests.

## Build plan

### A. Config + dependency (CR-0002, LLD §0)
- `src/agentkit/config.py`: add `extract_llm_base_url`, `extract_llm_model`,
  `extract_llm_api_key` (optional), defaulting `extract_llm_base_url`→`llm_base_url` and
  `extract_llm_model`→`llm_model` when unset (resolve in `get_settings`/helpers, not as
  literal field defaults). Add optional `extract_llm_price_in_per_1k` /
  `_out_per_1k` (default None → cost reported as "n/a").
- `.env.example`: add `EXTRACT_LLM_BASE_URL`, `EXTRACT_LLM_MODEL`, `EXTRACT_LLM_API_KEY`.
- `pyproject.toml`: add `openai`.

### B. Migration `0002_extract_staging.py` (down_revision `0001_init`)
- New table `staging.release_candidate` (`id text PK, client text, created_at
  timestamptz default now(), status text check in ('open','exported','imported',
  'promoted','abandoned') default 'open'`).
- `ALTER TABLE` each of the seven staging mirrors: add `conflict_group text`,
  `needs_family boolean not null default false`, `section_id text` (all nullable /
  defaulted so existing bootstrap-RC document rows are unaffected). Add an inline SQL
  comment: FK to `release_candidate` intentionally omitted; RC validity enforced at the
  release gate (LLD-REL, milestone 3b).
- Working `downgrade()` (drop columns, drop table). Round-trip covered by the existing
  `tests/db/test_migration_roundtrip.py` harness.

### C. Extractor endpoint client — `src/agentkit/extract/llm.py`
- OpenAI-compatible client built from `EXTRACT_LLM_*` (external-capable), temperature 0,
  JSON-schema-constrained responses (`response_format` json_schema), **retry-on-invalid
  max 3 then log + skip the section**. Log every call `{model, tokens_in, tokens_out,
  latency_ms}` to `llm-calls.jsonl`. Injectable `complete=` seam for offline tests.
  Framework-generic (no Jyotech strings).

### D. Markdown reader + section split — `src/agentkit/extract/markdown.py` (LLD-EXT-01)
- Parse `---`…`---` front-matter + body; split on H1–H3 headings into `Section{doc_id
  (via doc_id_for(url)), heading_path, page_range (from <!-- page N --> markers; HTML
  → locator = heading path), text}`. Persist to `sections.json`.

### E. Classifier — `src/agentkit/extract/classify.py` (LLD-EXT-02)
- Prompt (file) → `{type ∈ capability_spec|product_list|company_fact|office_contact|
  other, confidence}` per section; `other` skipped. Persist to `classification.json`.

### F. Pass 1 family discovery — `src/agentkit/extract/families.py` (LLD-EXT-03)
- One whole-corpus prompt proposes `product_family` candidates (id slug, division,
  category, name, summary, source). Write `clients/jyotech/families.yaml` and **STOP**.

### G. Schemas + evidence gate
- `clients/jyotech/schemas.py` (LLD-EXT-04…08): `Evidenced[T]={value,evidence}`;
  `CapabilityRowOut, ProductOut, CompanyFactOut, OfficeOut` + a `type→schema` registry.
- `src/agentkit/extract/evidence.py`: **verbatim gate in code** — evidence must be an
  exact substring of the section text after whitespace normalisation; a failing field is
  **dropped and logged** (section id + raw output), never "fixed". Client-agnostic.

### H. Pass 2 typed extraction — `src/agentkit/extract/extractors.py` (LLD-EXT-04…08)
- Per classified section, call the typed schema; apply the evidence gate; then apply
  `normalise.py` (numeric/unit, incl. new **"up to X"→max** parsing) in Python.
- **Frozen family** enforcement: assign only ids present in `families.yaml`; anything
  unassignable → `needs_family=true`, **never a new id**.
- **Known rules:** 20000 (web) vs 25000 (PDF) → extract **both** capability rows, set a
  shared `conflict_group`, don't resolve; F&S image-only dims → only what's literally in
  the text layer; company facts prefer about/contact sources where duplicated.
- Each staged row records `source_doc_id`, `source_locator`, `confidence`, `section_id`.

### I. Staging writers — `src/agentkit/extract/staging_write.py` (LLD-EXT-10, LLD-DB-06)
- Upsert the six content tables + a `staging.document` row per md under the **new RC**
  (identity/page_count from front-matter+manifest; title/division best-effort, may stay
  null for review) so the RC is a self-contained export unit. `review_status='pending'`.
- **RC idempotency:** re-running extraction for the same RC **replaces that RC's
  `pending` rows only**; rows already `approved`/`edited`/`rejected` (from a future
  import) are never overwritten (delete-pending-then-insert scoped by `review_status`).

### J. RC lifecycle + orchestrator + report
- `src/agentkit/release/candidate.py`: `create_rc(conn, client)` → allocates
  `rc.<client>.NNNN` (zero-padded, next after max existing), inserts ledger row; `list_rc`.
- `src/agentkit/extract/run.py`: orchestrates. **Gate logic:** `families.yaml` absent →
  run split+classify+pass 1, write families.yaml, STOP; present → run pass 2 + staging
  writes. `--redo-families` forces pass 1 again.
- `src/agentkit/extract/report.py`: run report — families proposed, per-table row
  counts, evidence-dropped count + examples, every `needs_family` + `conflict_group`
  row, and **token/cost totals** (aggregated from `llm-calls.jsonl`).

### K. Export — `src/agentkit/release/export.py` (LLD-REL-01)
- openpyxl xlsx, **one sheet per staging table**; columns: natural id, each field with
  its evidence quote beside the value, `source_doc_id`+`source_locator`, `confidence`,
  `conflict_group`, `needs_family`, `review_status`, and an **empty reviewer-decision
  column with data validation (approve/edit/reject)**. Frozen header row, sensible widths.
  Mark RC status → `exported`.

### L. CLI wiring
- `extract_app`: `extract run [CLIENT] --rc <id> [--redo-families]`.
- `release_app`: `release create [CLIENT]`, `release list`, `release export <rc>`.
- Follow the `connect()`/`conn.begin()`/`typer.echo` pattern exactly.

### M. Prompts (file-based)
- `clients/jyotech/prompts/`: `classifier`, `family_discovery`, and four extractor
  prompts. Loaded from files now; note in code + milestone note that these load into
  `ops.prompt_version` in a later milestone.

## Tests first (LLM mocked via `complete=` seam, fixtures)
`tests/extract/` (+ one in `tests/release/`):
- `test_extractor_endpoint.py` **(PRD-N-002, named in TRACEABILITY)** — extractor client
  built from `EXTRACT_LLM_BASE_URL`; runtime chat uses `LLM_BASE_URL`; embeddings
  `EMBED_BASE_URL`; unset extractor endpoint falls back to `LLM_BASE_URL`.
- `test_evidence_required.py` **(PRD-F-015, named in TRACEABILITY)** — verbatim gate
  accepts exact, rejects paraphrased, logs drops.
- `test_classifier.py` — routing on fixture sections (incl. `other` skipped).
- `test_frozen_family.py` — unknown id → `needs_family`, never invented.
- `test_normalise.py` — "up to X"→max + unit round-trips.
- `test_rc_idempotency.py` — re-run replaces `pending` only, preserves edited/rejected.
- `test_export.py` — xlsx structure (7 sheets, evidence-beside-value, validation col,
  frozen header).
- `test_extract_integration.py` — canned multi-section fixture → full staged set incl. a
  `conflict_group` pair and a `needs_family` row (uses a fresh RC on `seeded_conn`).
- `test_release_candidate.py` — `create`/`list` allocate `rc.<client>.NNNN`, ledger status.

## Doc updates (end of milestone)
- Write `docs/changes/milestone-3a-notes.md` in the established format: what was built,
  the flagged clarification for LLD-DB-06 (new staging columns + `release_candidate`
  ledger — **clarification path, not /prd-change**), the 3b promote-gate requirement
  (refuse RC without ledger row / wrong status), the 20000-vs-25000 conflict disposition,
  the file-artifact locations, and the **token/cost totals** from the live run.
- Update `docs/TRACEABILITY.md` **PRD-F-015** row (Code: `extract/`, `release/` export
  + candidate; Tests: the `tests/extract/*` above) and note PRD-N-002's new test.

## Verification (end to end)
1. `pytest -q` green (DB tests need `docker compose up -d`).
2. `agentkit db upgrade` applies `0002`; `test_migration_roundtrip` passes.
3. `agentkit release create jyotech` → `rc.jyotech.0001`; `agentkit release list` shows it.
4. `agentkit extract run --rc rc.jyotech.0001` → writes `families.yaml`, STOPS. User
   edits/approves. Re-run → both passes complete against the real corpus + real model.
5. `agentkit release export rc.jyotech.0001` → xlsx under `data/jyotech/extract/…`.
6. Present: families proposed, per-table row counts, evidence-dropped fields (with
   examples), every `needs_family` + `conflict_group` row, and API cost/token totals.
7. Confirm `facts.*` untouched (no writes anywhere this milestone).
