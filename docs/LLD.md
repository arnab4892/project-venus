---
document: LLD
product: Jyotech Agent
version: 1.10
aligned_to_hld: 1.2
aligned_to_prd: 1.2
status: Approved
date: 2026-08-29
changelog: see CHANGELOG.md
---

# Low-Level Design — Jyotech Agent (Iteration 1)

Stack: Python 3.12, LangGraph, SQLAlchemy + Alembic, Postgres 16 + pgvector, Docling (PDF/HTML → Markdown), Pydantic v2, FastAPI, Angular 18 web component. Endpoints (all OpenAI-compatible): the runtime chat LLM (`LLM_BASE_URL`) and the embedding model (`EMBED_BASE_URL`) are self-hosted; the offline extractor's LLM (`EXTRACT_LLM_BASE_URL`) may be an external API, used only over public website/catalogue content (no customer data). When `EXTRACT_LLM_BASE_URL` is unset it defaults to `LLM_BASE_URL`.

Repository layout (framework core + client registry):

```
src/agentkit/            # client-agnostic framework
  ingest/  extract/  release/  retrieval/  tools/  runtime/  handoff/  eval/
clients/jyotech/
  config.yaml            # client_id, domain, theme, handoff routing
  schemas.py             # extraction Pydantic models registered for this client
  prompts/               # seed prompt bodies loaded into ops.prompt_version
  seeds/                 # seed URLs, PDF list, region_state
  golden/questions.yaml
docs/                    # this folder
```

## 1. Database (LLD-DB)

| ID | Item | Implements |
|---|---|---|
| LLD-DB-01 | Migration `0000_bootstrap` creates the `vector` extension and the four empty schemas `facts`, `vec`, `staging`, `ops` (with working downgrade); migration `0001_init` then creates all tables and columns exactly as `design/data-model.md` §2–3. Extension and schema state live in migrations only — never in docker init SQL. | HLD-001 |
| LLD-DB-02 | Every `facts.*` table carries `release_id`; PK = `(natural_id, release_id)` (`capability_gas`: `(cap_id, release_id, gas)`); all intra-facts FKs are composite and same-release. Exactly one active release, enforced by a partial unique index on `release(is_active) WHERE is_active`. A `facts.active_<table>` view per table exposes the natural ids of the active release; the tool layer reads ONLY the views — no code outside the release module filters on `release_id`. (See the Keying rule note in `design/data-model.md` §2.) `capability_gas` deliberately carries no source columns in staging or facts: it is a pure child of `capability_row`, and its provenance is **inherited** through the composite same-release FK to its parent (data-model §2.4). | HLD-001, HLD-C-05 |
| LLD-DB-03 | `vec.chunk_embedding_<release>` created per release by the embedder; HNSW index `vector_cosine_ops`, `m=16, ef_construction=128`. | HLD-C-04 |
| LLD-DB-04 | `ops.message` has `turn_id` FK, `seq_in_turn`, `kind ∈ {text, document_card, status, form}`; a turn owns 1..n messages in either role. | HLD-C-06 |
| LLD-DB-05 | `ops.lead.reference_no` format `JYO-YYMM-NNNN`, sequence per client per month. | HLD-C-08 |
| LLD-DB-06 | `staging.*` mirrors only the seven content tables (`document`, `product_family`, `product`, `capability_row`, `capability_gas`, `company_fact`, `office`) — the tables whose rows are LLM-proposed and human-reviewed — keyed by `release_candidate_id`, plus `evidence jsonb`, `confidence numeric`, `review_status ∈ {pending, approved, edited, rejected}`, `reviewer`, `reviewed_at`. No staging mirror for `release` (written by promote itself) or `region_state` (curated). Staging carries NO cross-table FKs — it holds unreviewed and rejected rows; FK closure is enforced at the release-import gate (LLD-REL-03), not in staging. Migration `0002` adds three extraction markers to every mirror — `conflict_group text` (links same-family/same-measure rows whose sources disagree; both rows staged verbatim, adjudicated by the reviewer, never auto-resolved), `needs_family boolean not null default false` (row unassignable to a frozen family; `family_id` stays null, ids are never invented), `section_id text` (the extraction section the row came from) — and the RC ledger `staging.release_candidate` `(id, client, created_at, status ∈ {open, exported, imported, promoted, abandoned})`. The mirrors deliberately carry **no FK** to the ledger (same no-FK stance); RC validity and status are enforced at the promote gate (LLD-REL-05). The ingest bootstrap RC (`rc.<client>.bootstrap`) is valid without a ledger row and is never promotable. | HLD-C-02, C-03 |
| LLD-DB-07 | `facts.region_state` is curated configuration, not extracted content: columns `(state, release_id, region, office_id)` with composite FK to `office` and its own `active_` view; it carries no source columns (`source_doc_id` provenance is mandatory only for extracted facts) and enters via seed/release tooling, never via staging review. | HLD-C-08, HLD-001 |

## 2. Crawler & converter (LLD-ING) — implements HLD-C-01

| ID | Item |
|---|---|
| LLD-ING-01 | The shipped crawler is driven by `clients/jyotech/seeds/sources.yaml`: the full discovered source list (27 HTML/PHP pages + 2 PDF URLs as of 2026-08; the homepage primary nav carries 11 links, which was the earlier design-time count) plus an explicit `exclude:` list — exclusions are content-based, human-decided config, never filename-based. The seed list was bootstrapped by a one-time same-host depth-2 crawl following `.html/.php/.pdf`; a future `ingest discover` may regenerate it. Two live URLs contain literal spaces and one PDF name contains `&` — stored percent-encoded. |
| LLD-ING-02 | HTML: strip chrome via a per-client CSS selector list (incl. breadcrumbs and teaser-card sections) and per-client `drop_alt_text` / `drop_link_text` values for placeholder stubs ("Image", "Read More", "Responsive Image"); skip HTML comment nodes; keep headings, paragraphs, tables (→ pipe tables), meaningful image alt, absolutised PDF links. Generic rules live in code; selectors/values are client config. |
| LLD-ING-03 | PDF: Docling `DocumentConverter` with the **PyPdfiumDocumentBackend** (the default DoclingParse backend clips right-edge words), table structure on with `TableFormerMode.ACCURATE` and `do_cell_matching=False`; Markdown escaping off; OCR only for pages with text density < 50 chars/page. `tidy_markdown` collapses consecutive `<!-- image -->` placeholders and drops orphan single-character lines (table rows preserved). Tables whose data exists only as raster images are not reconstructable and are recorded in the report as published-as-image. |
| LLD-ING-04 | Output `data/<client>/md/<sha256>.md` with `<!-- page N -->` markers and front-matter `{url, kind, sha256, fetched_at, title}`. Idempotent on hash. |
| LLD-ING-05 | One `staging.document` row per source URL (byte-identical duplicate URLs share one converted file but each keeps its row), upserted under a per-client bootstrap RC id (`rc.<client>.bootstrap`, `--rc` overridable); `facts.document` is written only by release promotion. RC lifecycle is owned by LLD-REL. |
| LLD-ING-06 | `agentkit ingest verify` — mandatory post-ingest verifier and release precondition. PDF sources: an independent `pdftotext` (poppler) witness over the cached raw bytes yields a token multiset (numbers; units `Nm3/hr|lpm|Bar|Barg|kW|HP`; standards `API-618|ISO n:n|EN|NFPA`; models `MCH-*|ICON|VEGA|NOVA|NEPTUNE|PROEYE`) and every witness token must appear in the converted Markdown — image-only content is invisible to both sides, keeping the check honest. HTML sources: assert none of the banned junk patterns remain. Per-source PASS or exact missing tokens; non-zero exit on any failure. `ingest run` caches raw bytes to `data/<client>/raw/<sha>` and writes `data/<client>/ingest-manifest.json` to support offline verification. System dependency: poppler `pdftotext` (macOS `brew install poppler`; Linux `apt install poppler-utils`). |

## 3. Extractor (LLD-EXT) — implements HLD-C-02

The extractor's LLM calls (section classification LLD-EXT-02, family discovery LLD-EXT-03, typed extraction LLD-EXT-04) use `EXTRACT_LLM_BASE_URL`, which may be an external API (HLD-005 / PRD-N-002); inputs are the converted public Markdown only. Evidence-substring, drop-and-log and staging-only rules are unchanged regardless of provider.

Sampling is provider-configurable, not hard-coded: `EXTRACT_LLM_TEMPERATURE`, `EXTRACT_LLM_REASONING_EFFORT` and `EXTRACT_LLM_MAX_COMPLETION_TOKENS` are each sent **only when set** (blank env var = unset; some providers reject a fixed `temperature`, e.g. Moonshot kimi-k3 fixes it at 1.0) — faithfulness comes from strict `response_format` json_schema decoding plus the evidence gate, not from a temperature value. Invalid responses are retried at most 3 times, then the section is logged and skipped. Every call is logged to `data/<client>/extract/<rc>/llm-calls.jsonl` `{model, kind, section_id, tokens_in/out/cached, latency_ms, attempts, ok}`; cached-input tokens are detected across OpenAI/Moonshot/DeepSeek `usage` shapes (unknown shape → 0), and the run report prints token totals, prompt-cache hit rate and cost (cache-miss vs cache-hit input priced via `EXTRACT_LLM_PRICE_IN_PER_1K` / `_PRICE_CACHED_IN_PER_1K`; unset → "n/a").

| ID | Item |
|---|---|
| LLD-EXT-01 | `extract/markdown.py` reads the converted md (front-matter + body) and splits on headings (H1–H3); each section carries `section_id` (`<doc_id>::sNNN`), `doc_id`, `heading_path`, page range (from `<!-- page N -->`; HTML → heading-path locator) and verbatim text. Run artifacts are files under `data/<client>/extract/<rc>/`: `sections.json`, `classification.json`, `drops.json`, `llm-calls.jsonl` (gitignored, like ingest's `md/`/`raw/`). |
| LLD-EXT-02 | Section classifier prompt → `{type ∈ capability_spec, product_list, company_fact, office_contact, other, confidence}`. `other` is skipped for facts (still chunked). Classifications are cached in `classification.json` and reused on a pass-2 re-run. |
| LLD-EXT-03 | Pass 1 (family discovery): whole-corpus prompt proposes `product_family` candidates, written to `clients/<client>/families.yaml`, and the run **stops** for human review (`--redo-families` reruns pass 1). The approved list is frozen for pass 2: extractors assign only ids present in it; an unassignable row gets `needs_family=true`, never an invented id. |
| LLD-EXT-04 | Four extraction schemas in `clients/jyotech/schemas.py`, each field an `Evidenced[T] = {value, evidence}`. The verbatim gate lives in code (`extract/evidence.py`), not in the prompt: evidence must be an exact substring of the section (heading path + body) after whitespace normalisation, or the field is dropped and logged to `drops.json` — never repaired. |
| LLD-EXT-05 | `CapabilityRowOut`: family_id (from frozen list), comp_type, lubricated, cooling, capacity {min,max,unit}, discharge_p {min,max,unit}, driver[], standards[], gases[]. |
| LLD-EXT-06 | `ProductOut`: family_id, model_name (verbatim; **null when the catalogue prints no model number** — migration `0003` relaxed `facts.product.model_name` to nullable; a name is never invented), variant, description, attributes (only printed). At runtime a name-less product is presented by its family name + variant/description, never a blank or an invented name. |
| LLD-EXT-07 | `CompanyFactOut`: kind (enum: certification, founded, founder, facility, industry_served, client, coverage, contact), value, detail. |
| LLD-EXT-08 | `OfficeOut`: name, city, state, address, phone, email, serves_divisions[]. Phone validated E.164-ish; city must resolve via `region_state`. |
| LLD-EXT-09 | Numeric/unit normalisation in `extract/normalise.py`: "up to X" → max=X; "X to Y" → (min,max); unit tokens stripped before number parsing (so `Nm3`'s digit is never read as a value); Nm3/hr, Nm³/hr, NM3/HR → `Nm3/hr`; bar/barg → `barg`; SCMD kept, converted at query time with the documented constant `1 Nm3/hr = 24 SCMD` (day = 24 hours). |
| LLD-EXT-10 | Writes to `staging.*` with `release_candidate_id`; never to `facts.*`. RC idempotency: re-running extraction for an RC replaces only that RC's `pending` rows; `approved`/`edited`/`rejected` rows are never overwritten. A `staging.document` row per md is staged under the RC so it is a self-contained export unit. Prompts are file-based under `clients/<client>/prompts/` until the `ops.*` tables exist (`ops.prompt_version` loading is a later milestone). |

## 4. Review & release (LLD-REL) — implements HLD-C-03

| ID | Item |
|---|---|
| LLD-REL-01 | `agentkit release export <rc>` writes an .xlsx with one sheet per staging table: every value column carries its evidence quote in an adjacent `»evidence` column, plus provenance (`source_doc_id`, `source_locator`, `section_id`), `confidence`, `conflict_group`, `needs_family`, `review_status`, and an empty `reviewer_decision` column with an approve/edit/reject dropdown; frozen header row. Export advances the RC ledger status to `exported`. RC lifecycle lives in `release/candidate.py` (`release create` allocates `rc.<client>.NNNN`; `release list`). |
| LLD-REL-02 | `agentkit release import <rc> <xlsx>` applies `review_status`/edits (`approve`/`edit`/`reject`; blank = stays `pending`, not promoted); children of rejected rows (e.g. `capability_gas` under a rejected `capability_row`) must not survive to promote. |
| LLD-REL-03 | `agentkit release check <rc>` on the approved+edited set. **Hard failures:** reference closure (family ids in the frozen `families.yaml`; every `source_doc_id` — including each family's parsed source — is a surviving document); every `capability_gas` row has a surviving parent `capability_row`; provenance (`source_doc_id`+`source_locator`) present on the source-carrying tables (`product`, `capability_row`, `company_fact`, `office`); any capacity/discharge numeric without a unit (listed with its evidence quote); duplicate natural ids; `needs_family` still true on a survivor; a generic **facts NOT-NULL-columns-present-on-survivors** safety net (schema-driven, so promote never dies on a raw DB error); an office whose city does not resolve via `region_state` (curated per-client seed, e.g. `clients/jyotech/seeds/region_state.yaml`; the office→region mapping is printed for human eyeballing). **Warnings (non-blocking):** a family with no surviving row; surviving rows still sharing a `conflict_group` (reviewer kept both). |
| LLD-REL-04 | Diff vs active release: any changed numeric in `capability_row` is listed and must be acknowledged. |
| LLD-REL-05 | `agentkit release promote <rc>` → inserts approved/edited rows into `facts.*` under new `release_id`, triggers embedding (LLD-RET), runs golden suite (LLD-EVAL); `activate` flips `is_active`; `rollback <release>` flips back. Promote gate (supersedes the deliberately absent staging FK): **refuse any RC with no `staging.release_candidate` ledger row, or whose status is not `exported`/`imported`** — the bootstrap RC is thereby never promotable. Provenance at promote applies to the **tables carrying source columns** (see LLD-REL-03); `capability_gas` is gated relationally — a surviving gas row must have a surviving parent, provenance being transitive through the composite FK. `facts.product_family` is populated from the approved, frozen `clients/<client>/families.yaml` (staging's `product_family` mirror stays empty by design — families are curated input, not extracted content; a `source` entry may hold multiple `;`-separated tokens — the first is parsed into `source_doc_id`/`source_locator`). Release ids are date-based `rYYYY.MM.N` (data-model §2.1). |

## 5. Chunker & embedder (LLD-RET) — implements HLD-C-04

| ID | Item |
|---|---|
| LLD-RET-01 | Chunk by heading, target 400–600 tokens, 60-token overlap; a table is kept whole unless it would exceed the embed limit, in which case it is split by rows with the header row repeated in every part; `family_ids` tagged **mechanically, never by an LLM**: the active release's facts rows whose `source_doc_id` + section heading the chunk covers, ∪ frozen family-name matches in the chunk text. A chunk's `division` is derived via `family_ids → product_family.division` (documents carry no division of their own); the retrieval pre-filter joins through families. |
| LLD-RET-02 | Embedding model `bge-m3` (1024-d) via `EMBED_BASE_URL`; batch 64. The embedder MUST (a) set the provider context window explicitly per request (for Ollama: `num_ctx`, ≥ max chunk tokens + margin), (b) count tokens per chunk and raise `ChunkTooLargeError` if `token_count ≥ embed_limit` — never rely on provider-side silent truncation, and (c) log the effective limit at startup. Token counts use the real bge-m3 tokenizer, configured via `embed_tokenizer` (repo id, resolved from the local HF cache) or `embed_tokenizer_path` (local file, for air-gapped installs) — no per-request network (PRD-N-002). One golden question (LLD-EVAL-01) must target content at the END of the longest chunk as a truncation canary, pinned by **locator** (stable across re-chunking), never by chunk id. |
| LLD-RET-03 | Hybrid retrieval: cosine top-20 ∪ FTS top-20 → reciprocal rank fusion → top-k (k=5), with SQL pre-filter on `division` and `family_ids && :families`. |
| LLD-RET-04 | Per-document chunking disposition `chunk / reference_only / excluded`: seeded by junk heuristics (substantive word count after dropping headings/link-stubs/boilerplate; link-to-text ratio; fraction of paragraphs duplicated elsewhere in the corpus), written to the dedicated human-editable `clients/<client>/seeds/chunking.yaml` (not sources.yaml). `agentkit chunk run` writes the yaml + a disposition report and **stops** for human approval before any embedding; a re-run applies the approved yaml. `reference_only` keeps the document row and extracted facts but produces no chunks (e.g. the catalogue link-hub page). Plus corpus-wide paragraph-level dedup at chunk time: a normalised paragraph appearing in multiple documents is chunked once from a canonical source (prefer PDF/about page) and skipped elsewhere. |

## 6. Tool layer (LLD-TOOL) — implements HLD-C-05

All tools are pure functions over the `facts.active_*` views and return JSON. Logging to `ops.tool_call` begins in the runtime milestone (the `ops.*` tables are not yet created); until then the tools carry no side effects.

| ID | Tool | Signature → result |
|---|---|---|
| LLD-TOOL-01 | `match_capability(gas, capacity, capacity_unit, discharge_p, lubricated?, standard?)` → `{matches, non_comparable_candidates, any_near_edge}`. **`matches`** = rows where every requested comparable filter passed, ranked by fit; each match carries `{cap_id, family_id, headroom:{capacity, pressure}, near_edge, caveats}` — `headroom = 1 − value/limit` per dimension, **`near_edge` is per-match** (any of its own ratios > 0.9). A row that FAILS a comparable filter is excluded entirely. **`non_comparable_candidates`** = rows where a filter could not be evaluated (unit mismatch or null), listed with the reason, never presented as matches. Pure over the `facts.active_*` views; unit conversion per LLD-EXT-09 within a unit's own family only. Per-client gas alias map (`clients/<client>/config.yaml`: `hydrogen→H2`, …) applied at query time — facts stay verbatim. Null `lubricated` = "unspecified/both offered": kept under an oil-free filter with a `lubricated_unspecified` caveat. Null/unknown-unit numerics stay non-comparable (skipped, never guessed). |
| LLD-TOOL-02 | `list_products(division, category?)` → families + products. |
| LLD-TOOL-03 | `get_product(model_or_family)` → product ⋈ family; alias normalisation strips spaces/hyphens (`MCH16` = `MCH-16`). |
| LLD-TOOL-04 | `search_documents(query, division?, family_ids?, k=5)` → chunks with locator + url (LLD-RET-03). |
| LLD-TOOL-05 | `get_company_fact(kind)` → rows. |
| LLD-TOOL-06 | `get_office(city? | state? | region?)` → office; fallback head office. |

## 7. Orchestrator (LLD-RT) — implements HLD-C-06

Runtime prompts live in `ops.prompt_version`: `agentkit prompt load <client>` versions the file manifest into the DB, `agentkit prompt activate <id>` flips `is_active` **gated by the golden suite** (LLD-EVAL-03); the runtime reads only the active version (file fallback only when `ops` is empty, logged loudly), and every `ops.agent_invocation` records the prompt_version id it ran with.

| ID | Item |
|---|---|
| LLD-RT-01 | FastAPI `POST /v1/chat` `{session_id?, client_id, messages:[...]}` → SSE stream of `message` events (each with `kind`, `seq_in_turn`) and a final `turn` event. (Widget/API milestone; 5a delivers `agentkit chat <client> [--session <id>] [--show-trace]`, an interactive CLI loop over the same graph.) |
| LLD-RT-02 | LangGraph state: `{session, turn, user_messages[], triage, slots, agent, tool_results[], draft, grounding, outcome}`. As built (5a): a **thin in-memory StateGraph** — all persistence via explicit `ops.*` inserts in the nodes; `--session` resume rebuilds context from the ops rows. The Postgres checkpointer is deferred to the widget/API milestone (async resumption); the ops rows remain the **single source of truth** even after it lands. |
| LLD-RT-03 | Nodes: `ingest_user` → `triage` → `route` → `<agent>` → `ground` → `respond` | `handoff`. |
| LLD-RT-04 | Triage output schema `{division, intent ∈ {application_enquiry, product_question, documents, after_sales, commercial, faq, out_of_scope}, language, in_scope, pii_present, confidence}`; confidence < 0.6 → ask a one-line clarifying question (outcome `clarify`). |
| LLD-RT-05 | Grounding gate, as built (5a): an `action=answer` message must carry ≥1 citation resolving to a real `tool_result` id from this turn, **and** every number in the outgoing text must appear in this turn's tool results or retrieved chunks after unit-token stripping (**numeric guard** — an unsourced number is treated exactly like an uncited answer). Unbacked → strip and fall back to a "not in our published material" sentence + handoff offer. Result stored in `turn.grounding`. Per-claim splitting (this item's original wording) is a deferred refinement. |
| LLD-RT-06 | Every user message and every emitted message row is written before the SSE event is sent. |
| LLD-RT-07 | Language: triage detects; agent prompts include `respond_in`; retrieval query is translated to English when needed. |

## 8. Intent sub-agents (LLD-AG) — implements HLD-C-07

Each agent = prompt (from `ops.prompt_version`) + allowed tool list + output schema `{messages:[{kind,text|payload}], slots?, action ∈ {answer, ask_slot, handoff, deflect}, citations:[tool_result ids]}`.

| ID | Agent | Tools | Behaviour |
|---|---|---|---|
| LLD-AG-01 | application_discovery | 01, 04 | Slot order gas → capacity → discharge_p → lubricated → standard → industry → timeline; one question per turn, never guess a value; calls match_capability when gas+capacity+discharge_p known. **The top match's own row is the authority for every number in the answer** (published limits, never a quote); `search_documents` is called only after top-match selection, filtered to that family with the family name in the query, for supporting prose; a near-edge caveat only when the top match's own flag is true; customer-facing family names only (no `fam.*` ids, no slot vocabulary); non-comparable candidates at most one sentence offering engineer review. |
| LLD-AG-02 | product_advisor | 02, 03, 04 | Exact model names; attributes only if in tool result. |
| LLD-AG-03 | documents_compliance | 04, 05, 06 | Serves `document_card` messages for PDFs/certs; verbatim company facts. |
| LLD-AG-04 | after_sales_intake | 03, 06 | Collects model, serial/year, site, need, contact; no diagnosis; action handoff with `lead_type=after_sales`. |
| LLD-AG-05 | commercial_routing | 06 | Always handoff with `lead_type ∈ {commercial, dealer}`; never states price/lead time. |
| LLD-AG-06 | faq_deflect | 05 | Careers → career page; unrelated → polite scope statement. |

## 9. Handoff service (LLD-HO) — implements HLD-C-08

| ID | Item |
|---|---|
| LLD-HO-01 | Contact form delivered as a `form` message kind (name, company, phone, whatsapp_ok, email, city) with consent checkbox; free-text fallback parsed by the handoff prompt. |
| LLD-HO-02 | Region = `region_state(state)`; state inferred from city via a static city→state table, else asked. |
| LLD-HO-03 | Recipients by lead type. For `after_sales`: `to` = the published branch-office email for the region (`office.email` of the office `region_state(state).office_id`), `cc` = client `handoff_to.sales`; if no branch-office email is published, fall back to `to` = `handoff_to.sales` with the region in the subject. For other lead types: `to` = `handoff_to.sales` (+ office email if non-null). Subject `[{reference_no}] {lead_type label} – {summary} – {company} ({city}) – {REGION}`. |
| LLD-HO-04 | Email via SMTP relay (`SMTP_URL`); `handoff_dispatch` row written before send, status updated after; retry 3× with backoff; on final failure the user still receives the reference number and ops is alerted. |
| LLD-HO-05 | Emits `status` message "Sending…" then `text` confirmation (LLD-DB-04). |

## 10. Widget (LLD-UI) — implements HLD-C-09

| ID | Item |
|---|---|
| LLD-UI-01 | Angular element `<jyotech-agent client="jyotech">` loaded by `<script src=…/widget.js>`; theme from `GET /v1/client/{id}/theme`. |
| LLD-UI-02 | Renders by `kind`: text (markdown-lite), document_card (title, locator, open link), status (replaced in place by the next message of the turn), form (contact form). |
| LLD-UI-03 | Quick-start chips on open: Industrial compressors · Fire/Rescue/Diving · Service & spares · Documents. |
| LLD-UI-04 | Persistent "Talk to an engineer" button → `POST /v1/chat` with `action=handoff`. |
| LLD-UI-05 | Session id in a first-party cookie (`ja_sid`, 30 days); UA/IP captured server-side. |

## 11. Eval harness (LLD-EVAL) — implements HLD-C-10

| ID | Item |
|---|---|
| LLD-EVAL-01 | `clients/jyotech/golden/questions.yaml`: `{id, question, expected_answer_contains[], expected_source_ids[], expected_outcome}` plus optional `fact:`/`retrieval:` execution blocks the runner scores — frozen set of **46** (18 capability — incl. the 25000-capacity guard, the SCMD unit-conversion case and a Hinglish e2e-only question (PRD-F-012); 12 product — incl. price→handoff and the null-`model_name` case; 8 company/docs — incl. the locator-pinned truncation canary; 6 after-sales; 2 out-of-scope). Maintenance rule: natural ids pinned in the suite (`cf.*`, `off.*`, `cap.*`) follow extraction section numbering, so a re-extraction updates the suite alongside that release's diff review. |
| LLD-EVAL-02 | Three layers: fact-level (tool call returns expected ids), retrieval (expected chunk in top-k), end-to-end (agent answer contains/omits). |
| LLD-EVAL-03 | Run on `release activate` — the tools read the `active_*` views, so a release can only be evaluated once active: any fact/retrieval failure **rolls the activation back** (promote triggers embedding best-effort in its own transaction; an unreachable embedder warns, never rolls promote back). `prompt activate` is gated the same way from the runtime milestone. |

## 12. Revision history

| Version | Date | CR | Aligned to HLD / PRD | Summary |
|---|---|---|---|---|
| 1.10 | 2026-08-29 | — (clarification, from milestone-5a-notes) | 1.2 / 1.2 | Runtime as built: prompt versioning preamble (load/activate gated by the golden suite, agent_invocation records prompt_version); RT-01 CLI chat now / API-SSE at the widget milestone; RT-02 thin in-memory StateGraph with ops-inserts persistence, checkpointer deferred, ops = source of truth; RT-05 gate as built (citation + numeric guard, per-claim deferred); TOOL-01 result split into matches (per-match near_edge, caveats) vs non_comparable_candidates, alias map + null-lubricated caveat + extended vocabulary now implemented; AG-01 row-is-authority + family-scoped retrieval + presentation rules (from the live hydrogen fix cycle). |
| 1.9 | 2026-08-29 | — (clarification, from milestone-4-notes) | 1.2 / 1.2 | Retrieval/tools/eval as built: mechanical family tagging + derived chunk division (RET-01); bge-m3 tokenizer config + locator-pinned canary (RET-02); disposition in `chunking.yaml` with the chunk-run human gate (RET-04); tool logging deferred until `ops.*` exists (LLD-TOOL preamble); golden suite frozen at 46 with execution blocks + id-maintenance rule (EVAL-01); golden gate on `release activate` with rollback-on-failure, embedding best-effort at promote (EVAL-03). |
| 1.8 | 2026-08-29 | — (clarification, from milestone-3b-notes) | 1.2 / 1.2 | Release machinery as built: `capability_gas` provenance inherited via parent FK (LLD-DB-02); `model_name` nullable when unprinted, migration `0003` + runtime display rule (LLD-EXT-06); LLD-REL-03 rewritten as the implemented check (hard failures incl. unit-on-numerics and the schema-driven NOT-NULL safety net, warnings, office→region mapping); LLD-REL-05 provenance scoping, families.yaml multi-source parsing, `rYYYY.MM.N` ids; LLD-TOOL-01 non-comparable-unit rule + deferred alias-map/null-boolean/vocabulary design note. |
| 1.7 | 2026-08-28 | — (clarification, from milestone-3a-notes) | 1.2 / 1.2 | Extraction as built: `0002` staging additions (`conflict_group`, `needs_family`, `section_id`; `release_candidate` ledger, no-FK stance) in LLD-DB-06; provider-configurable sampling + call logging + cache-aware cost reporting in LLD-EXT §3; section/artifact details (EXT-01/02), families.yaml stop + frozen-family rule (EXT-03), code-level evidence gate (EXT-04), range parsing (EXT-09), RC idempotency + file-based prompts (EXT-10); export as built + RC lifecycle (REL-01), import decision semantics (REL-02), promote ledger-status gate + product_family from families.yaml (REL-05). |
| 1.6 | 2026-08-28 | CR-0002 | 1.2 / 1.2 | Endpoints preamble: extractor LLM via `EXTRACT_LLM_BASE_URL` (may be external, public content only; defaults to `LLM_BASE_URL`), runtime chat + embeddings stay self-hosted. LLD-EXT §3 note added. |
| 1.5 | 2026-08-23 | CR-0001 | 1.1 / 1.1 | LLD-HO-03: after-sales routes `to` the published branch-office email for the region (`office.email` via `region_state.office_id`), `cc` sales@, with sales@-with-region-in-subject as the fallback; other lead types unchanged. Reflects the PRD-F-006 modify. |
| 1.4 | 2026-08-23 | — (clarification, from milestone-2-notes) | 1.0 / 1.0 | Ingestion as built: sources.yaml-driven crawl with content-based excludes (LLD-ING-01), cleaner rules (ING-02), pypdfium backend + ACCURATE tables + tidy pass (ING-03), staging-only writes under bootstrap RC (ING-05), new LLD-ING-06 `ingest verify` (pdftotext witness; poppler dependency). New LLD-RET-04 design note: chunking disposition + paragraph dedup. |
| 1.3 | 2026-08-23 | — (clarification, from milestone-1-notes) | 1.0 / 1.0 | Implemented decisions folded in: composite keying + one-active-release index + views-only rule (LLD-DB-02); staging scope = seven content tables, no cross-table FKs (LLD-DB-06); region_state as curated no-source fact table (new LLD-DB-07); headroom formula (LLD-TOOL-01); SCMD constant (LLD-EXT-09). |
| 1.2 | 2026-08-23 | — (clarification) | 1.0 / 1.0 | LLD-DB-01: `0000_bootstrap` migration (vector extension + empty schemas) precedes `0001_init`; DB state lives in migrations, not docker init SQL. Confirmed in Step 0. |
| 1.1 | 2026-08-23 | — (clarification) | 1.0 / 1.0 | LLD-RET-01/02: explicit embed context (`num_ctx`), loud `ChunkTooLargeError` instead of silent truncation, table-split rule, truncation-canary golden question. No requirement or HLD change. |
| 1.0 | 2026-08-23 | — | 1.0 / 1.0 | Initial LLD |
