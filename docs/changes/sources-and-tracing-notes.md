---
title: Customer-facing sources resolver + dev-only Langfuse tracing
date: 2026-09-02
author: Claude Code (paired with arnab.sharma)
type: implementation note (additive runtime plumbing + dev tooling; no PRD/HLD/LLD design change)
lld_items: [LLD-RT-02, LLD-RT-05, LLD-AG-03]
prd_row: PRD-F-008
branch: feat/sources-resolver-langfuse-tracing
---

# Sources resolver + Langfuse tracing

Two independent, **code-only** additions on top of the merged polish pass. No prompt / golden /
schema / activation-gate change. Documents remain the source of truth; nothing in HLD/LLD required
editing — the two rule-6 flags below are for the next doc pass.

## Part 1 — customer-facing sources resolver (rule-6 flag: PRD-F-008 presentation / LLD-RT-05 rider)

Citations are developer-shaped ids (`cap.*` / `fam.*` / `ch.*`) and carry a `url` only on
`search_documents` chunks; the dev harness could name a source only when the same turn produced a
`document_card`. New runtime resolver turns a turn's citations into a customer-ready Sources list.

- `runtime/sources.py` — `resolve_sources(conn, citations) -> [{title, url, link, location, kind}]`.
  Every citation is walked to its **source document** via the read-only `facts.active_*` views:
  chunks via `doc_id`; capability rows / families / products / company facts / offices via their
  `source_doc_id`; a document citation is its own doc. Grouped so each distinct document appears
  once (first-cited order). `title` comes from `facts.active_document`, falling back to the
  URL-derived title helper (relocated here from `documents_compliance`, re-exported there). For a
  PDF, `location` is `p. N[–M] — Section` (page + heading parsed from the locator) and `link`
  appends `#page=N`; for a web page, `location` is the section label and `link` is the page url; a
  null/missing url renders the entry **without** a link.
- **Persist what the customer saw (LLD-RT-05 rider).** `orchestrator.n_ground` resolves the
  answer's citations and merges the list into the answer message payload
  (`ops.message.payload.sources` — JSONB, **no schema change**), so the conversation record keeps
  exactly what was shown. **Fail-open:** a resolver error is logged and the turn still answers with
  no sources. The raw `ops.citation` rows and the dev trace are untouched.
- The Gradio dev harness switched to this list: `apps/harness.py::build_sources` now surfaces
  `payload["sources"]` (duplicated citation/`document_card` join deleted; the harness stays
  DB-free and does not re-resolve), and `apps/dev_ui.py::_sources_markdown` renders **title +
  location as the link text**.
- **Tests:** `tests/runtime/test_sources.py` (grouping/dedupe across kinds; page-anchor derivation;
  web section label; null-title → URL fallback; null-url → no link; capability→parent-document);
  `tests/runtime/test_ground_sources.py` (payload merge on the answer path; n_ground fail-open when
  the resolver raises → turn still answers, no `sources` key); `tests/apps/test_dev_ui_adapter.py`
  (build_sources reads payload / empty when absent).

## Part 2 — dev-only Langfuse tracing (dev tooling; Langfuse lives OUTSIDE this repo)

Turn-level observability against an **existing external self-hosted Langfuse — server version 4**.
Langfuse runs as separate infrastructure; **this repo contains no docker-compose/deployment for
it** — only the SDK integration.

- **SDK pin:** `langfuse>=4.15,<5` in a new `dev-obs` optional-dependency extra (installed
  `langfuse==4.15.1`, the OTel-based v4 SDK matching the v4 server). `runtime/tracing.py` is
  written against the **installed** v4 API (`from langfuse import observe, get_client,
  propagate_attributes`; `langfuse.openai.OpenAI`), not the removed v2 `langfuse.decorators` API.
- `runtime/tracing.py` — central `tracing_enabled()` gate evaluated at **call** time; inert-by-
  default `observe` decorator (wraps `langfuse.observe` only when enabled, else runs the plain
  function); `force_off()` hard override; `trace_session()` / `annotate_trace()` for trace
  metadata. All langfuse imports are lazy and wrapped (fail-open; the extra is optional).
- **Env bridge (`ensure_configured`).** pydantic loads `LANGFUSE_*` from `.env` into `Settings`,
  but the langfuse SDK reads its credentials from `os.environ` **directly** — a value living only
  in `.env` never reaches the SDK, so its client would init "without public_key" and disable
  itself. When tracing is enabled, `ensure_configured()` exports the three settings into
  `os.environ` (idempotent) before the first langfuse client is constructed; every langfuse touch
  point (the `observe` wrapper, `trace_session`, `annotate_trace`, the `llm.py` client wrap) calls
  it first.
- **Wiring:** the runtime chat client (`runtime/llm.py::RuntimeClient._openai`) uses
  `langfuse.openai.OpenAI` when tracing is on (fail-open to plain `openai`); `@observe` on the five
  orchestrator nodes, the six tool functions, and `run_turn` as the trace **root** (one trace per
  turn). `run_turn` attaches `turn_id` / `session_id` / active `prompt_version` ids as trace
  metadata (ops stays the system of record).
- **Structural guard:** tracing activates only when `JYOTECH_DEV_UI=1` **and** all three
  `LANGFUSE_*` are set (`config.Settings`). Unset = fully off, zero overhead. Production tracing is
  a later deliberate decision.
- **Force-off (enforced in code):** `eval.runner.run_eval` calls `force_off()` at entry — the one
  chokepoint that covers `eval run`, `prompt activate` and `release activate` (all route through
  `run_eval`); an autouse session fixture in `tests/conftest.py` does the same for pytest.
  Rationale: eval turns run in a rolled-back savepoint, so their traces would reference ops rows
  that never commit; the eval report is already the complete record.
- **Tests:** `tests/runtime/test_tracing.py` — off without the dev flag (even with all keys); off
  without the keys (even with the dev flag); an eval run with the tracing env fully set emits
  nothing (force-off wins); fail-open (an `@observe`-wrapped function still returns when the
  langfuse layer raises / a dead host is configured).

## Verification

`pytest -q` green; one standalone `agentkit eval run jyotech` confirms the suite is unchanged and
emits no traces. Live: the first trace lands in the v4 UI as one trace per turn with node + tool +
LLM spans and `turn_id`/`session_id`/`prompt_ids` metadata; with `LANGFUSE_*` unset the behaviour
is identical and nothing is emitted.
