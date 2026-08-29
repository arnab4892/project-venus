---
title: Milestone 4 — chunking, embedding, retrieval, tool layer, golden suite
date: 2026-08-29
author: Claude Code (paired with arnab.sharma)
type: implementation note (deviations flagged for the next doc pass; no PRD/HLD change)
lld_items: [LLD-RET-01, LLD-RET-02, LLD-RET-03, LLD-RET-04, LLD-DB-03, LLD-TOOL-01, LLD-TOOL-02, LLD-TOOL-03, LLD-TOOL-04, LLD-TOOL-05, LLD-TOOL-06, LLD-EVAL-01, LLD-EVAL-02, LLD-EVAL-03]
prd_row: PRD-F-003/004/005 (tools), PRD-F-008 (citations), PRD-N-002 (self-hosted embeddings), PRD-N-005 (golden gate)
branch: milestone-4-retrieval
---

# Milestone 4 — chunking, embedding, retrieval, tool layer, golden suite

Sixth milestone in the CLAUDE.md build order. Milestone 3b promoted `r2026.08.2` into
`facts.*` and printed "embedding deferred"; this milestone turns that active release's
converted Markdown into chunks + embeddings, implements the complete read-only tool layer,
and stands up the golden suite at its fact and retrieval layers. The end-to-end
(agent-answer) eval layer needs the agents and is **out of scope** (milestone 5) — reported
`pending`, never failed. Orchestrator, handoff and widget are out of scope.

Two human STOP gates were honoured: **Gate 1** (chunking disposition report, before any
embedding) and **Gate 2** (golden `questions.yaml` review, before freezing). Both were
approved with edits (below).

## 1. What was implemented

### Chunking (`retrieval/chunk.py`, `retrieval/tokenizer.py`, LLD-RET-01/02/04)
- Reads the active release's documents (`facts.active_document` ⋈ `data/<client>/md/<sha256>.md`),
  splits on H1–H3 headings (reusing `extract/markdown.py`), packs 400–600-token windows with a
  60-token overlap; tables over the target are split by rows with the header repeated.
- **Per-document disposition** `chunk | reference_only | excluded` seeded by junk heuristics
  (substantive prose word count, link count, cross-document paragraph duplication), written to
  the human-editable `clients/jyotech/seeds/chunking.yaml` and approved at Gate 1.
  `reference_only` keeps the document + its facts but yields no chunks.
- **Corpus-wide paragraph dedup**: a paragraph appearing in several documents is chunked once
  from the canonical source (PDF preferred) and skipped elsewhere.
- **Mechanical family tagging** (never an LLM): `family_ids` = the active facts rows whose
  section (`source_doc_id` + heading) the chunk covers, ∪ frozen family-name matches.
- **Token guard (LLD 1.1 rule)**: counts tokens with the real bge-m3 tokenizer and raises a
  loud `ChunkTooLargeError` at `token_count ≥ embed_limit` — never silent truncation.
- CLI `agentkit chunk run jyotech` writes the yaml + a disposition report and **STOPS** (Gate 1).

### Embedding (`retrieval/embed.py`, LLD-RET-02 / LLD-DB-03)
- `EmbeddingClient` over the self-hosted `EMBED_BASE_URL` (bge-m3, 1024-d), `num_ctx` sent
  explicitly per request, batch 64, behind an `embed=` seam for tests.
- `agentkit release embed <release>` writes `facts.chunk` (+ FTS `tsv`) and creates
  `vec.chunk_embedding_<release>` (`vector(1024)`, HNSW `vector_cosine_ops`, `m=16`,
  `ef_construction=128`), verifies embedded count == chunk count, idempotent.
- `release promote` now **triggers embedding** best-effort in its own transaction (replacing the
  "embedding deferred" line); an unreachable embedder warns, never rolls the promote back.

### Retrieval + tools (`tools/`, `extract/normalise.py`, `client_config.py`, LLD-TOOL-01..06)
- `search_documents` — hybrid cosine top-20 ∪ FTS top-20 → RRF → top-k=5, `division`/`family_ids`
  pre-filter, returns chunk text + locator + url; query embedded with the same model (PRD-N-002).
- `list_products`, `get_product` (alias normalisation `MCH16`==`MCH-16`; null-`model_name`
  presented by family + variant per LLD-EXT-06), `get_company_fact`, `get_office`
  (head-office fallback; region/state via `active_region_state`).
- `match_capability` upgraded per the LLD-TOOL-01 design note: per-client **gas alias map**
  (`clients/jyotech/config.yaml`, `hydrogen`→`H2` … applied at query time, facts verbatim);
  null `lubricated` = "unspecified/both offered" — kept under an oil-free filter, flagged
  `lubricated_unspecified`; the 3b non-comparable-unit skip retained.
- `extract/normalise.py` vocabulary extended (`cfm`, `lpm`, `lumen`, `tons`, `TPD`, `kg/hr`, `W`,
  `m3/hr`, `SCMH`; pressures `psi`, `kg/cm2g`): comparable only within their own canonical unit,
  no invented cross-unit factor, null/unknown still non-comparable.

### Golden suite (`eval/runner.py`, `clients/jyotech/golden/questions.yaml`, LLD-EVAL-01/02/03)
- 46 questions (see §2) scored at the **fact** layer (a named tool returns the expected ids) and
  the **retrieval** layer (expected chunk in top-k); the **e2e** layer is `pending`.
- `agentkit eval run jyotech` runs both layers; `release activate` runs the same suite as a gate
  and rolls the activation back on any fact/retrieval failure (LLD-EVAL-03).

### Tests (test-first): `pytest -q` → **164 passed**, DB tests on `seeded_conn`
`tests/retrieval/test_chunk.py`, `test_embed.py`; `tests/tools/test_search_documents.py`,
`test_tools.py`, extensions to `test_match_capability.py`; `tests/extract/test_normalise.py`
extensions; `tests/eval/test_runner.py`. The embedder is mocked via the `embed=` seam; the
PRD-N-002 separation test asserts the client resolves `EMBED_BASE_URL`, never `LLM_BASE_URL`.

## 2. Deviations & notes (rule 6 — flag, do not edit LLD.md)

1. **Disposition config** lives in `clients/jyotech/seeds/chunking.yaml`, not `sources.yaml` as
   LLD-RET-04 phrases it — a dedicated human-editable file per the milestone brief.
2. **`chunk.division` is derived, not stored.** `facts.chunk` (migration 0001) has no `division`
   column and all 26 documents carry a null division, so division is derived from
   `family_ids → product_family.division`; the retrieval pre-filter joins through families.
3. **`family_ids` from promoted facts.** LLD-RET-04 says "staged rows of the same RC"; because
   chunking targets the promoted active release, the equivalent join is the active facts rows
   (`source_doc_id` + section heading) those staged rows became — same mechanism.
4. **`ops.tool_call` logging deferred** to the runtime milestone (`ops.*` tables are not created
   yet); the tools are pure JSON-returning functions this milestone.
5. **Full RET-03 hybrid implemented** (cosine ∪ FTS → RRF → k=5), richer than the brief's
   "vector top-k" summary.
6. **Golden gate wired into `release activate`, not `promote`.** The tools read the
   `active_*` views, so the suite can only evaluate a release once it is active; promote triggers
   embedding, activate runs the golden gate (LLD-EVAL-03: "any failure blocks activation").
7. **bge-m3 tokenizer via configurable id/path**, not a 16 MB vendored blob: `embed_tokenizer`
   (repo id, resolved from the local HF cache) / `embed_tokenizer_path` (local file for
   air-gapped installs). Runtime uses no per-request network (PRD-N-002).
8. **Gate-2 six-question extension.** The suite is 46, not the initial 40: added
   `prod-price-handoff` (price never invented → handoff), `aftersales-service-kolkata`,
   `cap-process-max-capacity` (permanent 20000-vs-25000 guard on the 25000 row),
   `cap-natgas-unit-conversion` (2000 Nm3/hr = 48000 SCMD, in range), `prod-nameless-cabinet`
   (null-`model_name` display rule), `e2e-hinglish-hydrogen` (PRD-F-012, e2e-only).
9. **Truncation canary pinned by locator, not chunk id.** "AMCA certified" occurs only at the
   tail of the longest chunk (F&S catalogue intro, 820 tokens); the canary pins it by its stable
   `expect_locator` (F&S §heading) because chunk ids renumber when chunking config changes — the
   canary must fail only on real truncation.
10. **Maintenance note.** Natural ids pinned in the suite (`cf.*`, `off.*`, `cap.*`) are tied to
    extraction section numbering, so a future re-extraction changes them and the suite is updated
    alongside that release's diff review — an expected cost, not a surprise.
11. **Embed endpoint for the live run.** The config default `embed.internal:8001` is a
    placeholder; the live embed + eval runs pointed `EMBED_BASE_URL` at the on-host Ollama
    (`http://localhost:11434/v1`) serving bge-m3.

## 3. Live run status (active release `r2026.08.2`, dev Postgres)

### Gate 1 — dispositions (approved with 2 edits)
26 documents → **23 chunk, 2 excluded, 1 reference_only** = **138 chunks**.
- auto-`excluded`: `doc.contact` (36-word stub).
- human edits at Gate 1: `doc.gallery_test2` → `excluded` (lorem-ipsum placeholder page),
  `doc.catalogue_testing` → `reference_only` (catalogue download hub; prose duplicates the PDFs).
- corpus dedup: **1 paragraph** (a survivor-detector line shared by the F&S catalogue PDF and
  `doc.fire_protection_disaster_management_equipment`, kept in the PDF).
- largest chunk: **820 tokens** (F&S catalogue intro, limit 8192) — well under the hard limit.

### Embedding
**138/138** chunks embedded → `vec.chunk_embedding_r2026_08_2` (1024-d, HNSW cosine). Count
parity verified. Cosine sanity: `search_documents("hydrogen fuelling station")` returns
`doc.jyotech_catalog_process p12 §HYDROGEN FUELLING SYSTEMS` at rank 1.

### Gate 2 — golden suite (approved with the §2.8 extension)
46 questions (18 capability, 12 product, 8 company/docs, 6 after-sales, 2 out-of-scope).
`agentkit eval run jyotech` against the live release: **fact 39 pass / 7 na, retrieval 6 pass /
40 na, e2e 46 pending → RESULT: PASS**. (fact `na` = questions with no fact block, incl. the
e2e-only and out-of-scope items; retrieval `na` = questions with no retrieval block.) The
hydrogen conversation (`cap-hydrogen-process`) returns `fam.process_recip` via
`cap.doc_jyotech_catalog_process_s005.0` (headroom capacity 0.88, pressure 0.65 — consistent with
the 3b live smoke); the AMCA truncation canary retrieves the F&S intro passage.

## 4. Exit criteria status

- `pytest -q` green (**164 passed**). ✓
- Gate-1 disposition report approved and embedded; `vec.chunk_embedding_r2026_08_2` populated
  (138/138). ✓
- Gate-2 `questions.yaml` approved and frozen (46 questions). ✓
- `agentkit eval run jyotech` green on fact + retrieval layers against the live active release;
  e2e reported pending. ✓
- CLI demo: `search_documents("hydrogen fuelling station")` → PROCESS-catalogue chunk with
  locator; `get_office(region='South')` → Chennai. ✓

## 5. Traceability
`docs/TRACEABILITY.md` updated: LLD-RET (chunking/embedding/retrieval), LLD-TOOL-01..06 and
LLD-EVAL-01..03 gain their Code/Tests cells under PRD-F-003/004/005, PRD-F-008, PRD-N-002 and
PRD-N-005.
