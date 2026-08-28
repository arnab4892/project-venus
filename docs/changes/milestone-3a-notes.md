---
title: Milestone 3a — extraction to staging + review export
date: 2026-08-28
author: Claude Code (paired with arnab.sharma)
type: implementation note (+ flagged LLD-DB-06 clarification; no PRD/HLD change)
lld_items: [LLD-EXT-01, LLD-EXT-02, LLD-EXT-03, LLD-EXT-04, LLD-EXT-05, LLD-EXT-06, LLD-EXT-07, LLD-EXT-08, LLD-EXT-09, LLD-EXT-10, LLD-REL-01, LLD-DB-06]
prd_row: PRD-F-015 (+ PRD-N-002 first extractor-endpoint test)
branch: milestone-3a-extraction-staging
---

# Milestone 3a — extraction to staging + review export

Fourth milestone in the CLAUDE.md build order: turn the review-ready Markdown
from milestone 2 into **evidenced, reviewable facts** in `staging.*`, and export
them for human review. A minimal release-candidate lifecycle wraps it. Scope ends
at the exported spreadsheet — import, integrity checks, diff and promote/activate
are milestone 3b. **Nothing is written to `facts.*` anywhere in this milestone**
(the extractor and staging writers touch `staging.*` only). Two human gates:
after pass 1 (the proposed family list) and after export.

The extractor's LLM calls use `EXTRACT_LLM_BASE_URL` (CR-0002) — which may be an
external API, over public website/catalogue content only. When unset it falls
back to the self-hosted runtime chat endpoint; embeddings and runtime chat are
untouched.

## 1. What was implemented

### Config + dependency (CR-0002, LLD §0) — `src/agentkit/config.py`
- `extract_llm_base_url` / `extract_llm_model` / `extract_llm_api_key`, resolved
  via `Settings.extractor_endpoint()` with fallback to `llm_base_url` / `llm_model`.
  Optional `extract_llm_price_in_per_1k` / `_out_per_1k` drive cost reporting
  (unset → cost reported as "n/a"; token totals always reported).
- `.env.example` gains the `EXTRACT_LLM_*` block. `openai` added to `pyproject.toml`.

### Migration `0002_extract_staging.py`
- New `staging.release_candidate` ledger (`id, client, created_at, status ∈
  {open, exported, imported, promoted, abandoned}`).
- Three extraction markers added to all seven staging content mirrors:
  `conflict_group text`, `needs_family boolean NOT NULL DEFAULT false`,
  `section_id text` (nullable/defaulted, so existing bootstrap-RC document rows
  are unaffected). Working `downgrade()`; covered by the existing round-trip test.

### Extractor pipeline — `src/agentkit/extract/` (client-agnostic; no Jyotech strings)
- `llm.py` — OpenAI-compatible client from `EXTRACT_LLM_*`; `temperature=0`,
  `response_format=json_schema`; **retry-on-invalid max 3 then log + skip** the
  section (`ExtractionSkipped`); per-call `{model, tokens, latency}` logging;
  injectable `complete=` seam for offline tests.
- `markdown.py` (LLD-EXT-01) — the md reader ingest never grew: parses the
  five-field front-matter and splits the body on **H1–H3** into sections with
  `doc_id` (via `doc_id_for`), `heading_path`, page range (from `<!-- page N -->`)
  and verbatim text. `document_records()` builds `staging.document` identity rows.
- `classify.py` (LLD-EXT-02) — per-section `{type, confidence}`; `other` skipped.
- `families.py` (LLD-EXT-03) — pass-1 whole-corpus proposal → `families.yaml` → STOP.
- `evidence.py` (LLD-EXT-04) — **the verbatim gate in code**: evidence must be an
  exact substring of the section text after whitespace normalisation; a failing
  field is dropped and logged (section id + raw), never "fixed".
- `normalise.py` (LLD-EXT-09) — extended with range parsing: `"up to X" → max=X`,
  `"X to Y" → (min,max)`, unit tokens stripped so `Nm3`'s digit isn't read as a value.
- `extractors.py` (LLD-EXT-04…08) — pass-2 typed extraction; gate → normalise →
  client row mapping; corpus-wide `conflict_group` linking.
- `staging_write.py` (LLD-EXT-10) — upsert under the RC; **RC idempotency**:
  delete the RC's `pending` rows then insert `ON CONFLICT DO NOTHING`, so reviewed
  (approved/edited/rejected) rows are never overwritten.
- `registry.py` / `prompts.py` — load the client schema module and prompt files.
- `run.py` — orchestrator + pass-1 gate; `report.py` — the run report.

### Client-specific — `clients/jyotech/`
- `schemas.py` (LLD-EXT-04…08) — `Evidenced[T]`, the four typed container schemas,
  the `type→schema` registry, `build_rows` (frozen-family enforcement → `needs_family`,
  unit/range normalisation, id derivation, provenance) and `mark_conflicts`.
- `prompts/` — `classifier`, `family_discovery`, and four `extract_*` bodies
  (file-based for now; loaded into `ops.prompt_version` in a later milestone).

### Release tooling — `src/agentkit/release/`
- `candidate.py` — `create_rc` (`rc.<client>.NNNN`), `list_rc`, `get_rc`, `set_status`.
- `export.py` (LLD-REL-01) — one xlsx, one sheet per staging table; every value
  column carries an adjacent `»evidence` quote; provenance, confidence,
  `conflict_group`, `needs_family`, `review_status`, and an empty
  reviewer-decision column with an approve/edit/reject dropdown; frozen header,
  sized columns; advances the RC to `exported`.

### CLI — `src/agentkit/cli.py`
- `agentkit extract run [CLIENT] --rc <id> [--redo-families]`,
  `agentkit release create|list [CLIENT]`, `agentkit release export <rc>`.

### Tests (test-first, LLM mocked via the `complete=` seam, 41 new)
`tests/extract/`: `test_extractor_endpoint` (PRD-N-002 — endpoint separation +
fallback), `test_evidence_required` (PRD-F-015 — verbatim gate), `test_markdown`,
`test_classifier`, `test_normalise`, `test_frozen_family`, `test_rc_idempotency`,
`test_extract_integration` (canned multi-section corpus → full staged set incl. a
conflict pair + a needs_family row). `tests/release/`: `test_release_candidate`,
`test_export`. `pytest -q` → **92 passed**.

## 2. Deviations & notes

- **Staging schema additions fold into LLD-DB-06 via the clarification path
  (CLAUDE.md rule 6), NOT `/prd-change`.** `conflict_group` / `needs_family` /
  `section_id` on the mirrors and the `staging.release_candidate` ledger are
  **added implementation detail** — no requirement or HLD decision changes. They
  are recorded here for the next doc pass to fold into LLD-DB-06 / data-model §2
  (the same way milestone 2's `ingest verify` is flagged for LLD-ING). `LLD.md`
  was not edited in this code milestone.
- **No FK from the staging mirrors to `release_candidate` — by design.** Staging is
  the waiting room for unreviewed/rejected rows; integrity is enforced at the
  release gate, not here. The migration and `candidate.py` carry a code comment to
  that effect. **Consequence for milestone 3b:** `release promote` MUST refuse any
  RC that has no ledger row, or whose status is not `exported`/`imported` — a
  status-aware check an FK could not give us. The ingest bootstrap RC
  (`rc.jyotech.bootstrap`) stays valid for ingest without a ledger row.
- **Run artifacts are files under `data/<client>/extract/<rc>/`** (`sections.json`,
  `classification.json`, `drops.json`, `llm-calls.jsonl`) — consistent with
  ingest's `md/` / `raw/` / `ingest-manifest.json`, and git-ignored. Classification
  is cached there and reused on a pass-2 re-run (skip re-classifying) unless
  `--redo-families`. **Token/cost totals are summarised into the run report and
  here** (not left only in the gitignored log).
- **The 20000-vs-25000 process-gas conflict** (milestone-2 §7) is handled per the
  rule: both rows are extracted verbatim and linked by a shared `conflict_group`
  (`cg.<family>.capacity`); nothing is resolved — the reviewer adjudicates. The
  extractor prompt explicitly tells the model to quote each source as-is and never
  reconcile. Proven in `test_extract_integration` (20000 + 25000 both staged, linked).
- **F&S image-only spec sheets** — extraction sees only the converted Markdown, so
  image-embedded dimension grids are already invisible; the prompts additionally
  forbid reading values out of images. No prose-mining beyond the text layer.
- **Frozen family** — pass 2 assigns only ids present in the approved
  `families.yaml`; an unassignable row is flagged `needs_family=true` with
  `family_id` left null, never an invented id (`test_frozen_family`).
- **openai dependency** added and installed (`openai 1.109.1`). The client opens no
  connection at construction, so config/endpoint separation is asserted offline.
- **Prompts are file-based** for now (`clients/jyotech/prompts/`); loading them into
  `ops.prompt_version` is a later milestone (the `ops.*` tables do not exist yet).
- **Extractor sampling is provider-configurable (clarifies LLD-EXT §3's "temperature
  0" — rule 6, not `/prd-change`).** The chosen extractor `EXTRACT_LLM_MODEL`
  (Moonshot `kimi-k3`) **rejects `temperature`** (fixed at 1.0, must be omitted), so
  a hard-coded `temperature=0` cannot be sent. The client now assembles request
  params via `build_request_kwargs` and sends each sampling knob **only when set**:
  `EXTRACT_LLM_TEMPERATURE` (blank → omitted; a self-hosted model may set 0),
  `EXTRACT_LLM_REASONING_EFFORT` (kimi-k3 `low|high|max`, sent via `extra_body`;
  blank → provider default), `EXTRACT_LLM_MAX_COMPLETION_TOKENS`. For kimi-k3,
  faithful/low-variance extraction comes from schema-constrained decoding + the
  fixed temperature 1.0 + reasoning effort, not from temperature 0. `response_format`
  is unchanged (strict `json_schema`, which is exactly kimi-k3's shape). `Settings`
  uses `env_ignore_empty` so a blank optional env var means "unset" rather than a
  coercion error. **To fold into LLD-EXT §3 at the next doc pass.** Tests:
  `tests/extract/test_llm_request.py`, extended `test_extractor_endpoint.py`.
- **Prompt-cache-aware cost reporting.** kimi-k3 does automatic prefix caching, and
  the extractor's calls already lead with the static prefix (prompt body + frozen
  family catalogue) and end with the varying section text, so repeated prefixes hit
  the cache. The client captures cached-input tokens from the response `usage`
  (`cached_input_tokens()` checks the OpenAI `prompt_tokens_details.cached_tokens`,
  a flat `cached_tokens`, DeepSeek/Moonshot `prompt_cache_hit_tokens`, and the
  Anthropic-style `cache_read_input_tokens` mirror; unknown → 0). **Confirmed live:
  k3 uses `prompt_tokens_details.cached_tokens`** (see §3). `CallLog.totals()` reports
  a cache hit rate and splits input cost into cache-miss (`EXTRACT_LLM_PRICE_IN_PER_1K`)
  vs cache-hit (`EXTRACT_LLM_PRICE_CACHED_IN_PER_1K`, the ~10× discount; unset → miss
  rate). The run report prints a "prompt cache" line. Tests: `test_llm_request.py`
  cache-accounting cases.

## 3. Live run status

The extractor endpoint was configured to **Moonshot `kimi-k3`**
(`EXTRACT_LLM_BASE_URL=https://api.moonshot.ai/v1`, `reasoning_effort=high`,
`temperature` omitted) and the **real-model pass 1 was run** (2026-08-28).

**Endpoint smoke + cache probe (live).** A single call confirmed connectivity/auth,
strict `json_schema` structured output (valid JSON back), `extra_body.reasoning_effort`
accepted, and `temperature` correctly omitted. A 2-call probe with a shared prefix
confirmed k3's prompt cache: call 2 reported `prompt_tokens_details.cached_tokens =
512` on a 579-token prompt (256-token-chunk aligned), and `cached_input_tokens()`
read it correctly. The detector also now covers the Anthropic-style
`cache_read_input_tokens` mirror. **So the cached-token field is confirmed for k3
(`prompt_tokens_details.cached_tokens`)** — the earlier "confirm against a live
response" caveat is resolved.

**Pass 1 (family discovery) — real run, `rc.jyotech.0001`.**
`agentkit release create jyotech` → `rc.jyotech.0001`; `agentkit extract run --rc
rc.jyotech.0001` split the real 26-doc corpus into **178 sections**, classified each,
proposed families, wrote `clients/jyotech/families.yaml`, and STOPPED at the gate.
- Classifier spread: `capability_spec` 46 · `product_list` 44 · `company_fact` 14 ·
  `office_contact` 5 · `other` 69 (skipped); mean confidence **0.81**.
- **59 families proposed** (industrial 20 · fire_rescue 33 · diving 6) — far more
  granular than the design-time sample of 12 (k3 split by product variant, e.g. the
  MCH breathing-air line into 6 families). Awaiting human review/merge before pass 2.
- Run health: **179 LLM calls, 0 skipped** — every call returned schema-valid JSON.
- Live totals: **129,788 in / 15,032 out tokens; 41,728 cached input tokens (32%
  cache hit rate); est. cost $0.5022** (at the `EXTRACT_LLM_PRICE_*` rates in `.env`).
  The 32% hit rate is the shared classifier prompt prefix being served from k3's cache.

**Gate-scope defect found + fixed during pass 2.** The first pass-2 run flagged 134
evidence drops (83 on `family`) and 96 `needs_family` rows, and the 20000/25000
conflict failed to link. Root cause: the evidence gate validated against
`section.text` (body only), but each section's **heading** lives in `heading_path`,
not the body — so the model's correct heading-cited evidence for `family` /
`model_name` / `comp_type` was wrongly dropped. Fix (code, not a design change — the
LLD-EXT-04 verbatim rule is unchanged; we corrected *what counts as the section's
verbatim text*): added `Section.evidence_text` = heading + body and fed that to the
gate. Raw LLM outputs are now persisted to `raw-extractions.json` so a future gate
tweak can be re-applied offline without re-calling the model.

**Pass 2 (typed extraction → staging), post-fix, `rc.jyotech.0001`.** 109 calls, 0
skipped, **59% cache hit, $1.74**. Per-table staged (all `pending`): document 26,
product 111, capability_row 67, capability_gas 81, company_fact 134, office 12
(`product_family` 0 — families are the curated frozen input, not extracted content).
Post-fix quality: **evidence drops 134 → 2** (both legit paraphrases), **`needs_family`
96 → 11** (genuine: heading-less `§(preamble)` product lists), **`conflict_group`
rows 19**. The **20000-vs-25000 process case now links**: `cg.fam.process_recip.
capacity` groups the PDF row (25000, §PROCESS COMPRESSORS (RECIP.)) and the web row
(20000, §PROCESS GAS COMPRESSORS), both verbatim, unresolved.

**Export.** `agentkit release export rc.jyotech.0001` → 431 rows across 7 sheets
(`review-rc.jyotech.0001.xlsx`), evidence beside each value, `conflict_group` /
`needs_family` / `review_status` columns, approve/edit/reject dropdown, frozen header;
RC status → `exported`. `capability_row.family_id` populated 66/67. `facts.*` untouched
(still 6 documents). **This completes milestone 3a's scope** (review spreadsheet
produced); import/integrity/promote are 3b.

Pipeline correctness is also proven deterministically by `test_extract_integration`
and the focused unit tests (incl. the new heading-scope gate cases).

## 4. Exit criteria status
- RC lifecycle (`create` / `list`) + extraction takes `--rc`; bootstrap RC ingest-only ✔
- Extractor endpoint separate + fallback (CR-0002), tested ✔
- Section split + classifier + pass-1 family gate (STOP at `families.yaml`) ✔
- Verbatim evidence gate in code (drop + log, never fixed) ✔
- Frozen-family enforcement (`needs_family`, never invented) ✔
- 20000-vs-25000 both extracted + `conflict_group`, unresolved ✔
- Staging-only writes; RC idempotency (replace pending, preserve reviewed) ✔
- Export xlsx (one sheet/table, evidence beside value, decision validation) ✔
- Provider sampling knobs (reasoning_effort/temperature/max_completion_tokens) +
  prompt-cache-aware cost reporting; k3 cached-token field confirmed live ✔
- Evidence gate validates against heading + body (`Section.evidence_text`); raw
  outputs persisted for offline re-gate ✔
- `pytest -q` green (104) ✔ · ruff clean ✔
- Real-model **pass 1** (59 families) + **pass 2** (post-fix: drops 2, needs_family 11,
  conflict rows 19, incl. the linked 20000/25000 pair) + **export** (431 rows, 7 sheets)
  against kimi-k3 ✔ (see §3) — milestone-3a scope complete; import/promote = 3b

## 5. Traceability
`PRD-F-015` row updated — Code: `extract/` (llm, markdown, classify, families,
evidence, extractors, normalise, staging_write, registry, prompts, run, report),
`clients/jyotech/schemas.py` + `prompts/`, `release/candidate.py`,
`release/export.py`, `migrations/versions/0002_extract_staging.py`, cli
`extract run` / `release create|list|export`; Tests: `tests/extract/*`,
`tests/release/*`. `PRD-N-002` gains its first test
(`tests/extract/test_extractor_endpoint.py`).
