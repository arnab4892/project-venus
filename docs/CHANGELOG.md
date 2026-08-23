# Changelog

One entry per document version. Newest first.

## 2026-08-23

- **LLD 1.3** — clarification (no CR), reconciling the docs with milestone 1 as merged (see `changes/milestone-1-notes.md`): composite `(natural_id, release_id)` keying, one-active-release partial unique index and views-only access in LLD-DB-02; staging narrowed to the seven content tables with no cross-table FKs in LLD-DB-06; new LLD-DB-07 for `region_state` as curated, source-less configuration; headroom formula in LLD-TOOL-01; SCMD constant (1 Nm³/hr = 24 SCMD) in LLD-EXT-09. `design/data-model.md` corrections (keying-rule note, `fam.search_eq`, `doc.index`/`doc.contact`) were made in the milestone-1 commit itself.

- **LLD 1.2** — clarification (no CR): LLD-DB-01 now records the `0000_bootstrap` migration (vector extension + four empty schemas, reversible) preceding `0001_init`, and that DB state belongs to migrations, never docker init SQL. Decision confirmed during Step 0.
- **LLD 1.1** — clarification (no CR): LLD-RET-01/02 now require an explicit embed context window (`num_ctx` for Ollama), a loud `ChunkTooLargeError` instead of silent truncation, the split-long-tables-by-rows rule, and a truncation-canary golden question. Process rule 6 (LLD clarification path) added to docs/README.md.

- **PRD 1.0** — initial approved requirements for Iteration 1 (public content only). Tag `docs/prd-v1.0`.
- **HLD 1.0** — aligned to PRD 1.0. Decisions HLD-001…009, components HLD-C-01…10.
- **LLD 1.0** — aligned to HLD 1.0 / PRD 1.0.
- **TRACEABILITY** — generated.
