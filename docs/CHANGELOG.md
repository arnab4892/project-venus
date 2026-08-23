# Changelog

One entry per document version. Newest first.

## 2026-08-24

- **LLD 1.2** — clarification (no CR): LLD-DB-01 now records the `0000_bootstrap` migration (vector extension + four empty schemas, reversible) preceding `0001_init`, and that DB state belongs to migrations, never docker init SQL. Decision confirmed during Step 0.
- **LLD 1.1** — clarification (no CR): LLD-RET-01/02 now require an explicit embed context window (`num_ctx` for Ollama), a loud `ChunkTooLargeError` instead of silent truncation, the split-long-tables-by-rows rule, and a truncation-canary golden question. Process rule 6 (LLD clarification path) added to docs/README.md.

## 2026-08-22

- **PRD 1.0** — initial approved requirements for Iteration 1 (public content only). Tag `docs/prd-v1.0`.
- **HLD 1.0** — aligned to PRD 1.0. Decisions HLD-001…009, components HLD-C-01…10.
- **LLD 1.0** — aligned to HLD 1.0 / PRD 1.0.
- **TRACEABILITY** — generated.
