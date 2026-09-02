---
title: Housekeeping fix pass — staging timestamps, eval retry, fallback outcome, thinking knob
date: 2026-09-02
author: Claude Code (paired with arnab.sharma)
type: implementation note (four self-contained maintenance items; no agent/prompt behaviour change; rule-6 flags for the next doc pass)
lld_items: [LLD-DB-06, LLD-EVAL-02, LLD-EVAL-03, LLD-RT-05, LLD-EVAL-01]
prd_row: PRD-F-015 (staging), PRD-N-005 (eval), PRD-F-008/F-009 (grounding fallback), PRD-N-002 (runtime LLM)
branch: housekeeping-fix-pass
---

# Housekeeping fix pass

Four accumulated maintenance items, one commit each, **no agent or prompt behaviour change**. Each
carries a rule-6 flag where it deviates from or extends the current LLD wording. Verified with
`pytest -q` and one standalone `agentkit eval run jyotech` (three-layer green) — see §Verification.

## Item 1 — staging timestamps (migration 0005) [rule-6: LLD-DB-06]

`staging.*` was the only mutable store with no per-row chronology. Added `created_at` / `updated_at`
(both `timestamptz`, server-default `now()`) to the seven content mirrors (`document`,
`product_family`, `product`, `capability_row`, `capability_gas`, `company_fact`, `office`) and
`updated_at` to `staging.release_candidate` (it already had `created_at` from `0002`).

- **`facts.*` deliberately untouched** — fact rows are immutable; their history is
  `facts.release.built_at` + the release diff, not a per-row mutation stamp. `ops.*` supplies its own
  timestamps and is append-only, so it was not touched either.
- **Maintenance is at the app UPDATE sites, not a trigger.** All staging writes are raw SQL (there is
  no ORM, so a SQLAlchemy `onupdate` can never fire) and the codebase has no trigger convention. The
  columns default on INSERT (so `extract/staging_write.py`'s delete-then-reinsert rewrite gets a fresh
  `created_at` for free); the three in-place UPDATE sites set `updated_at = clock_timestamp()`
  explicitly: `ingest/staging_write.py` (`_UPSERT` DO-UPDATE), `release/import_review.py` (`_apply` +
  the `capability_gas` rejection cascade), and `release/candidate.py` (`set_status`).
- **`clock_timestamp()`, not `now()`, on update** so the stamp is the real modification instant and
  strictly advances even within a single transaction (`now()` is fixed at transaction start; an
  in-transaction rewrite would otherwise read `updated_at == created_at`).
- **Backfill caveat.** Rows written before `0005` receive `created_at = updated_at = migration time`
  from the server default — timestamps predating this migration are **not** true chronology; only rows
  written after it carry real creation history.
- Migration is reversible (`downgrade` drops the columns). Natural ids stay text (already true).

Tests: `tests/db/test_migration_roundtrip.py` (column presence after upgrade, clean up/down roundtrip)
and `tests/release/test_release_candidate.py::test_status_update_advances_updated_at_but_not_created_at`
(an `UPDATE` through `set_status` advances `updated_at` while `created_at` holds).

## Item 2 — eval e2e retry-once flaky policy [rule-6: LLD-EVAL-02/03]

The e2e layer runs the live runtime LLM and carries a known ~1-per-run transient that would
intermittently fail the golden gate on otherwise-good runs. Absorbed **by mechanism, not judgement**,
in `eval/runner.py`:

- **e2e layer only.** A failed e2e attempt is retried **exactly once**. If the retry passes, the
  question is `FLAKY` — it counts as a pass for the gate (`EvalReport.ok` treats `flaky` as non-fail)
  but the report keeps the **first** attempt's evidence, prefixed `flaky (passed on retry)`, so the
  variance is never hidden. **Two consecutive failures is a real `FAIL`** and blocks exactly as
  before. Each attempt already runs in its own rolled-back savepoint, so the retry is independent and
  still writes no `ops.*` row. Implemented by extracting the run+score body into `_e2e_attempt` and
  wrapping the retry decision in `_e2e_layer`.
- **Fact/retrieval never retry** — they are deterministic; the retry path lives only in the e2e layer.
- **Summary surfaces flaky counts.** The per-layer summary now reads `passed/applicable` with a flaky
  suffix, e.g. `e2e   53/53, 1 flaky` (applicable excludes na/skip; passed = pass + flaky), so a
  masked transient is always visible in the report.

Tests (`tests/eval/test_runner.py`, scripting fail-then-pass via a FIFO `answer` queue): retry-pass →
`flaky`, `report.ok` True, first-attempt evidence labelled; two failures → `fail`, gate blocks; a
deterministic fact failure stays `fail` and never goes flaky.

## Item 3 — outcome = `fallback` on gate strip [rule-6: LLD-RT-05 / LLD-EVAL-01]

When the grounding gate strips a draft and ships the fallback sentence, the turn was previously
recorded as `answered` — indistinguishable in ops monitoring from a real answer. Now it is recorded
as its own outcome, `fallback`.

- `runtime/orchestrator.py::n_ground`: the `GroundingResult.ok` flag already distinguishes a passing
  answer from a stripped one, so the single unconditional `wf.outcome = "answered"` becomes
  `"answered" if gr.ok else "fallback"`. Nothing else in the node changes (the sources block already
  guards on `wf.citations`, empty on a stripped turn).
- **No migration.** `ops.turn.outcome` is untyped `TEXT` with no CHECK constraint, so the value is
  accepted as-is; `persist_turn` writes it unchanged. The trace (`cli.py::_print_trace`) and Gradio
  harness display outcome via generic passthrough, so both learn `fallback` for free.
- **Eval vocabulary** (`eval/runner.py::_OUTCOME_MAP`) gains `"fallback": "fallback"` so a fallback
  turn maps into the golden vocabulary and never silently mismatches an expectation. The golden
  header comment documents the new value.
- **Golden audit — clean.** The 53 goldens expect only `{answer×47, handoff×3, out_of_scope×2,
  asked_slot×1}`; none expect `fallback`, and turns that pass today never hit the strip path (a
  stripped turn already fails `expected_answer_contains`), so no expectation was edited.

Test: `tests/runtime/test_ungrounded_claims_removed.py::test_full_turn_ships_fallback_not_fabrication`
now also asserts `result.outcome == "fallback"`.
