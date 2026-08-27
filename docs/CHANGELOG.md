# Changelog

One entry per document version. Newest first.

## 2026-08-28

- **PRD 1.2 · HLD 1.2 · LLD 1.6** — CR-0002 (`changes/CR-0002.md`), propagated: PRD-N-002 scoped — self-hosted-model + no-customer-data-egress applies to the customer-facing runtime; the offline content-extractor may use an external API LLM on public content only (embeddings stay self-hosted). HLD-005 scoped + HLD-006/§5 data-egress notes; LLD endpoints preamble adds `EXTRACT_LLM_BASE_URL` (external-capable, defaults to `LLM_BASE_URL`) with an LLD-EXT §3 note; `config/` + `extract/` gain the extractor endpoint (new test `tests/extract/test_extractor_endpoint.py`). TRACEABILITY realigned PRD 1.2 → HLD 1.2 → LLD 1.6. Tag `docs/prd-v1.2` after commit.

## 2026-08-23

- **PRD 1.1 · HLD 1.1 · LLD 1.5** — CR-0001 (`changes/CR-0001.md`), propagated: after-sales routes to the published branch-office email for the customer's region, copying sales@ (PRD-F-006 modified; assumption A-1 updated). HLD-C-08 region-routing wording refined; **LLD-HO-03** recipient selection reworked (after-sales `to` = branch-office email via `region_state.office_id`, `cc` = sales@, fallback sales@) — LLD-HO-04 mailer gains `cc`. TRACEABILITY realigned PRD 1.1 → HLD 1.1 → LLD 1.5. Tag `docs/prd-v1.1` after commit.
- **LLD 1.4** — clarification (no CR), reconciling docs with milestone 2 as merged (see `changes/milestone-2-notes.md`): LLD-ING-01 corrected from "11 HTML URLs" to the sources.yaml-driven list (27 pages + 2 PDFs) with human-decided `exclude:`; cleaner rules in ING-02; pypdfium backend, ACCURATE table mode and tidy pass in ING-03; staging-only writes under the bootstrap RC in ING-05; **new LLD-ING-06**: `agentkit ingest verify` (independent pdftotext token witness; requires poppler — `brew install poppler` / `apt install poppler-utils`); **new LLD-RET-04** design note for the chunking milestone (per-document disposition + corpus-wide paragraph dedup).
- **LLD 1.3** — clarification (no CR), reconciling the docs with milestone 1 as merged (see `changes/milestone-1-notes.md`): composite `(natural_id, release_id)` keying, one-active-release partial unique index and views-only access in LLD-DB-02; staging narrowed to the seven content tables with no cross-table FKs in LLD-DB-06; new LLD-DB-07 for `region_state` as curated, source-less configuration; headroom formula in LLD-TOOL-01; SCMD constant (1 Nm³/hr = 24 SCMD) in LLD-EXT-09. `design/data-model.md` corrections (keying-rule note, `fam.search_eq`, `doc.index`/`doc.contact`) were made in the milestone-1 commit itself.

- **LLD 1.2** — clarification (no CR): LLD-DB-01 now records the `0000_bootstrap` migration (vector extension + four empty schemas, reversible) preceding `0001_init`, and that DB state belongs to migrations, never docker init SQL. Decision confirmed during Step 0.
- **LLD 1.1** — clarification (no CR): LLD-RET-01/02 now require an explicit embed context window (`num_ctx` for Ollama), a loud `ChunkTooLargeError` instead of silent truncation, the split-long-tables-by-rows rule, and a truncation-canary golden question. Process rule 6 (LLD clarification path) added to docs/README.md.

- **PRD 1.0** — initial approved requirements for Iteration 1 (public content only). Tag `docs/prd-v1.0`.
- **HLD 1.0** — aligned to PRD 1.0. Decisions HLD-001…009, components HLD-C-01…10.
- **LLD 1.0** — aligned to HLD 1.0 / PRD 1.0.
- **TRACEABILITY** — generated.
