---
document: LLD
product: Jyotech Agent
version: 1.1
aligned_to_hld: 1.0
aligned_to_prd: 1.0
status: Approved
date: 2026-08-24
changelog: see CHANGELOG.md
---

# Low-Level Design — Jyotech Agent (Iteration 1)

Stack: Python 3.12, LangGraph, SQLAlchemy + Alembic, Postgres 16 + pgvector, Docling (PDF/HTML → Markdown), Pydantic v2, FastAPI, Angular 18 web component. LLM and embeddings via an OpenAI-compatible internal endpoint (`LLM_BASE_URL`, `EMBED_BASE_URL`).

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
| LLD-DB-01 | Schemas `facts`, `vec`, `staging`, `ops` created by Alembic migration `0001_init`. Tables and columns exactly as `design/data-model.md` §2–3. | HLD-001 |
| LLD-DB-02 | `facts.*` tables carry `release_id`; a view `facts.active_*` per table filters to `release.is_active = true`. Tools read only the views. | HLD-001, HLD-C-05 |
| LLD-DB-03 | `vec.chunk_embedding_<release>` created per release by the embedder; HNSW index `vector_cosine_ops`, `m=16, ef_construction=128`. | HLD-C-04 |
| LLD-DB-04 | `ops.message` has `turn_id` FK, `seq_in_turn`, `kind ∈ {text, document_card, status, form}`; a turn owns 1..n messages in either role. | HLD-C-06 |
| LLD-DB-05 | `ops.lead.reference_no` format `JYO-YYMM-NNNN`, sequence per client per month. | HLD-C-08 |
| LLD-DB-06 | `staging.*` mirrors `facts.*` plus `evidence jsonb`, `confidence numeric`, `review_status ∈ {pending, approved, edited, rejected}`, `reviewer`, `reviewed_at`. | HLD-C-02, C-03 |

## 2. Crawler & converter (LLD-ING) — implements HLD-C-01

| ID | Item |
|---|---|
| LLD-ING-01 | Seeds from `clients/jyotech/seeds/sources.yaml`: 11 HTML URLs + 2 PDF URLs. Same-host crawl, depth 2, follows `.html/.php/.pdf`. |
| LLD-ING-02 | HTML: strip nav/footer/scripts via a per-client CSS selector list; keep headings, paragraphs, tables, image alt. |
| LLD-ING-03 | PDF: Docling `DocumentConverter` with table structure on; OCR enabled only for pages where text density < 50 chars/page. |
| LLD-ING-04 | Output `data/<client>/md/<sha256>.md` with `<!-- page N -->` markers and front-matter `{url, kind, sha256, fetched_at, title}`. Idempotent on hash. |
| LLD-ING-05 | A `facts.document`/`staging.document` row per source. |

## 3. Extractor (LLD-EXT) — implements HLD-C-02

| ID | Item |
|---|---|
| LLD-EXT-01 | Section split on headings (H1–H3); each section carries `doc_id`, `heading_path`, page range. |
| LLD-EXT-02 | Section classifier prompt → `{type ∈ capability_spec, product_list, company_fact, office_contact, other, confidence}`. `other` is skipped for facts (still chunked). |
| LLD-EXT-03 | Pass 1 (family discovery): whole-document prompt proposes `product_family` candidates; human confirms list; list is frozen for pass 2. |
| LLD-EXT-04 | Four extraction schemas in `clients/jyotech/schemas.py`, each field an `Evidenced[T] = {value, evidence}`; evidence must be a verbatim substring of the section text or the field is dropped and logged. |
| LLD-EXT-05 | `CapabilityRowOut`: family_id (from frozen list), comp_type, lubricated, cooling, capacity {min,max,unit}, discharge_p {min,max,unit}, driver[], standards[], gases[]. |
| LLD-EXT-06 | `ProductOut`: family_id, model_name (verbatim), variant, description, attributes (only printed). |
| LLD-EXT-07 | `CompanyFactOut`: kind (enum: certification, founded, founder, facility, industry_served, client, coverage, contact), value, detail. |
| LLD-EXT-08 | `OfficeOut`: name, city, state, address, phone, email, serves_divisions[]. Phone validated E.164-ish; city must resolve via `region_state`. |
| LLD-EXT-09 | Numeric/unit normalisation in `extract/normalise.py`: "up to X" → max=X; Nm3/hr, Nm³/hr, NM3/HR → `Nm3/hr`; bar/barg → `barg`; SCMD kept, converted at query time (1 SCMD ≈ 1/24 Nm3/hr, documented constant). |
| LLD-EXT-10 | Writes to `staging.*` with `release_candidate_id`; never to `facts.*`. |

## 4. Review & release (LLD-REL) — implements HLD-C-03

| ID | Item |
|---|---|
| LLD-REL-01 | `agentkit release export <rc>` writes an .xlsx with one sheet per staging table, columns incl. evidence and page link. |
| LLD-REL-02 | `agentkit release import <rc> <xlsx>` applies `review_status`/edits. |
| LLD-REL-03 | Integrity checks: FK closure, at least one capability row per industrial family, every office city resolves, no duplicate model names. |
| LLD-REL-04 | Diff vs active release: any changed numeric in `capability_row` is listed and must be acknowledged. |
| LLD-REL-05 | `agentkit release promote <rc>` → inserts approved rows into `facts.*` under new `release_id`, triggers embedding (LLD-RET), runs golden suite (LLD-EVAL); `activate` flips `is_active`; `rollback <release>` flips back. |

## 5. Chunker & embedder (LLD-RET) — implements HLD-C-04

| ID | Item |
|---|---|
| LLD-RET-01 | Chunk by heading, target 400–600 tokens, 60-token overlap; a table is kept whole unless it would exceed the embed limit, in which case it is split by rows with the header row repeated in every part; `family_ids` tagged from the frozen family list by name match + extractor output. |
| LLD-RET-02 | Embedding model `bge-m3` (1024-d) via `EMBED_BASE_URL`; batch 64. The embedder MUST (a) set the provider context window explicitly per request (for Ollama: `num_ctx`, ≥ max chunk tokens + margin), (b) count tokens per chunk and raise `ChunkTooLargeError` if `token_count ≥ embed_limit` — never rely on provider-side silent truncation, and (c) log the effective limit at startup. One golden question (LLD-EVAL-01) must target content at the END of the longest chunk as a truncation canary. |
| LLD-RET-03 | Hybrid retrieval: cosine top-20 ∪ FTS top-20 → reciprocal rank fusion → top-k (k=5), with SQL pre-filter on `division` and `family_ids && :families`. |

## 6. Tool layer (LLD-TOOL) — implements HLD-C-05

All tools are pure functions over the `facts.active_*` views, return JSON, and log to `ops.tool_call`.

| ID | Tool | Signature → result |
|---|---|---|
| LLD-TOOL-01 | `match_capability(gas, capacity, capacity_unit, discharge_p, lubricated?, standard?)` → `{matches:[{cap_id, family_id, headroom:{capacity, pressure}}], near_edge: bool}`; near_edge when any ratio > 0.9. Unit conversion per LLD-EXT-09. |
| LLD-TOOL-02 | `list_products(division, category?)` → families + products. |
| LLD-TOOL-03 | `get_product(model_or_family)` → product ⋈ family; alias normalisation strips spaces/hyphens (`MCH16` = `MCH-16`). |
| LLD-TOOL-04 | `search_documents(query, division?, family_ids?, k=5)` → chunks with locator + url (LLD-RET-03). |
| LLD-TOOL-05 | `get_company_fact(kind)` → rows. |
| LLD-TOOL-06 | `get_office(city? | state? | region?)` → office; fallback head office. |

## 7. Orchestrator (LLD-RT) — implements HLD-C-06

| ID | Item |
|---|---|
| LLD-RT-01 | FastAPI `POST /v1/chat` `{session_id?, client_id, messages:[...]}` → SSE stream of `message` events (each with `kind`, `seq_in_turn`) and a final `turn` event. |
| LLD-RT-02 | LangGraph state: `{session, turn, user_messages[], triage, slots, agent, tool_results[], draft, grounding, outcome}`; checkpointer = Postgres (ops schema). |
| LLD-RT-03 | Nodes: `ingest_user` → `triage` → `route` → `<agent>` → `ground` → `respond` | `handoff`. |
| LLD-RT-04 | Triage output schema `{division, intent ∈ {application_enquiry, product_question, documents, after_sales, commercial, faq, out_of_scope}, language, in_scope, pii_present, confidence}`; confidence < 0.6 → ask a one-line clarifying question (outcome `clarify`). |
| LLD-RT-05 | Grounding gate: draft is split into claims; each claim must cite a `tool_result` id; uncited factual claims are removed and, if any were removed, a "not published" sentence + handoff offer is appended. Result stored in `turn.grounding`. |
| LLD-RT-06 | Every user message and every emitted message row is written before the SSE event is sent. |
| LLD-RT-07 | Language: triage detects; agent prompts include `respond_in`; retrieval query is translated to English when needed. |

## 8. Intent sub-agents (LLD-AG) — implements HLD-C-07

Each agent = prompt (from `ops.prompt_version`) + allowed tool list + output schema `{messages:[{kind,text|payload}], slots?, action ∈ {answer, ask_slot, handoff, deflect}, citations:[tool_result ids]}`.

| ID | Agent | Tools | Behaviour |
|---|---|---|---|
| LLD-AG-01 | application_discovery | 01, 04 | Slot order gas → capacity → discharge_p → lubricated → standard → industry → timeline; one question per turn; calls match_capability when gas+capacity+discharge_p known; ranges stated as *published limits*, never as a quote. |
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
| LLD-HO-03 | `route_to` = client `handoff_to.sales` + office email if non-null; subject `[{reference_no}] {lead_type label} – {summary} – {company} ({city}) – {REGION}`. |
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
| LLD-EVAL-01 | `clients/jyotech/golden/questions.yaml`: `{id, question, expected_answer_contains[], expected_source_ids[], expected_outcome}` — initial set of 40 (15 capability, 10 product, 8 company/docs, 5 after-sales, 2 out-of-scope). |
| LLD-EVAL-02 | Three layers: fact-level (tool call returns expected ids), retrieval (expected chunk in top-k), end-to-end (agent answer contains/omits). |
| LLD-EVAL-03 | Run on `release promote` and on `prompt activate`; any failure blocks activation. |

## 12. Revision history

| Version | Date | CR | Aligned to HLD / PRD | Summary |
|---|---|---|---|---|
| 1.1 | 2026-08-24 | — (clarification) | 1.0 / 1.0 | LLD-RET-01/02: explicit embed context (`num_ctx`), loud `ChunkTooLargeError` instead of silent truncation, table-split rule, truncation-canary golden question. No requirement or HLD change. |
| 1.0 | 2026-08-22 | — | 1.0 / 1.0 | Initial LLD |
