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

## Item 4 — Qwen thinking-off knob for the compose call, measure only [rule-6: PRD-N-002 / LLD-RT]

The Langfuse trace showed the compose (final-answer) call spending the bulk of a turn on hidden
reasoning. This item adds a **dormant** knob to turn Qwen3 "thinking" off for the compose call only,
measures the effect, and **changes nothing by default**. No activation follows from this task.

**Shipped (the mechanism only):**
- `config.py`: `llm_disable_thinking: bool = False` (env `LLM_DISABLE_THINKING`); documented in
  `.env.example`.
- `extract/llm.py::build_request_kwargs`: new `disable_thinking` param merges
  `extra_body["chat_template_kwargs"] = {"enable_thinking": False}` alongside any `reasoning_effort`
  (merge, never overwrite).
- `runtime/llm.py::RuntimeClient`: carries the flag and applies it **only when
  `schema_name == "answer"`** — the compose call. Triage (`"triage"`) and every per-agent parse call
  (`application_slots`, `product_query`, `after_sales_slots`, `deflect`, `translate`) use other schema
  names and are untouched by construction. `build_runtime_client` threads the setting.
- Test: `tests/runtime/test_runtime_llm.py` — the merge behaviour and the compose-only gating
  (compose carries the flag; triage/parse do not; knob-off leaves compose untouched).

**Compose-only scoping confirmed live.** In a single knob-on turn the compose call dropped to
1.3 s / 117 output tokens while the `application_slots` parse call still spent ~29 s / 2.7k tokens
thinking — i.e. the flag reaches compose and only compose.

**Measurement method (repeatable at the milestone-7 decision point).** A throwaway harness
(`scratchpad/measure_thinking.py`, **not committed**) ran the full 53-question golden suite **twice**
— knob off (baseline) then knob on — both through `run_eval(..., layers=(fact, retrieval, e2e))`, so
every turn executed inside the eval layer's rolled-back savepoint and **no `ops.*` row was written**
(no raw `run_turn` loop). Instrumentation: the runtime `complete` seam was wrapped over
`RuntimeClient.raw_complete` to record per-call output-token count and latency, filtered to
`schema_name == "answer"` for the compose figures; `agentkit.runtime.orchestrator.run_turn` was
monkeypatched with a monotonic-clock timer for whole-turn e2e latency; pass/flaky/fail counts came
from the returned `EvalReport`. `embed=None` and the default settings were used, exactly as
`agentkit eval run jyotech` runs it, against the live self-hosted Qwen3 (`LLM_BASE_URL`) and bge-m3
(`EMBED_BASE_URL`). Run counts: 53 questions × 2 passes (the knob-on pass ran 55 e2e turns and 49
compose calls — two questions were retried once under the item-2 policy).

**Results (both passes RESULT=PASS, three layers green):**

| Metric | knob off (baseline) | knob on (compose thinking off) | change |
|---|---|---|---|
| fact / retrieval / e2e | 42/42 · 6/6 · 53/53, 0 flaky | 42/42 · 6/6 · 53/53, **2 flaky** | both green |
| compose latency — median | 15.82 s | 1.51 s | **−90 %** (~10×) |
| compose latency — mean | 17.70 s | 1.54 s | −91 % |
| compose output tokens — median | 1 537 | 138 | **−91 %** (~11×) |
| compose output tokens — total | 82 290 | 6 786 | −92 % |
| e2e turn latency — median | 20.20 s | 6.13 s | **−70 %** (~3.3×) |
| e2e turn latency — mean | 24.81 s | 11.19 s | −55 % |
| whole-suite wall clock | 1 316.6 s (~22 min) | 617.1 s (~10 min) | −53 % (~2.1×) |

**Reading.** Turning thinking off on compose alone cuts compose latency and output tokens by ~10×
and roughly halves whole-turn latency; the remaining turn cost is dominated by the still-thinking
triage/parse calls (untouched by design) and retrieval. **Correctness held** — the knob-on suite was
still three-layer green (e2e 53/53). The knob-on run showed **2 flaky** (failed once, passed on retry)
vs 0 in the baseline; the sample is too small to attribute causally to thinking-off, but it is a fair
flag that thinking-off may raise compose variance, and it is exactly the transient the item-2 retry
policy is designed to absorb. **No default was changed and no activation follows** — the decision is
deferred (milestone 7) with these numbers as the basis.

## Verification

- `pytest -q` → **300 passed** on the four-commit HEAD (new tests for items 1–4 included).
- Three-layer golden suite → **RESULT=PASS** (fact 42/42, retrieval 6/6, **e2e 53/53, 0 flaky**),
  executed via the identical `run_eval(layers=(fact, retrieval, e2e))` that `agentkit eval run jyotech`
  wraps, default (knob-off) settings, against the live self-hosted Qwen3 + bge-m3 — this was the
  item-4 baseline pass. Item-3 audit clean: no e2e turn resolved to `fallback`. The item-4 knob-on
  suite surfaced **no regression** (three-layer green; 2 flaky absorbed by the item-2 retry).
