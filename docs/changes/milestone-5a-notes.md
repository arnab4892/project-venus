---
title: Milestone 5a — ops schema, orchestrator, triage, capability + FAQ agents, grounding gate, CLI chat
date: 2026-08-29
author: Claude Code (paired with arnab.sharma)
type: implementation note (deviations flagged for the next doc pass; no PRD/HLD change)
lld_items: [LLD-DB-04, LLD-RT-02, LLD-RT-03, LLD-RT-04, LLD-RT-05, LLD-RT-06, LLD-RT-07, LLD-AG-01, LLD-AG-06, LLD-TOOL (logging begins), LLD-EVAL-03]
prd_row: PRD-F-001 (triage), PRD-F-002 (application slots), PRD-F-008 (citations), PRD-F-009 (grounding), PRD-F-012 (Hinglish, partial), PRD-F-013 (deflect), PRD-N-002 (self-hosted runtime LLM), PRD-N-005 (prompt-activate gate)
branch: milestone-5a-runtime
---

# Milestone 5a — runtime: ops schema, orchestrator, triage, capability + FAQ agents, grounding gate, CLI chat

Seventh milestone in the CLAUDE.md build order. Milestones 0–4 built the offline pipeline and
the read-only tool layer; nothing yet let a person converse with the system. 5a stands up the
runtime: the `ops.*` schema the runtime writes to, prompt versioning in the DB, a LangGraph
orchestrator (triage → route → agent → grounding gate → respond) with per-turn `ops`
persistence, the two in-scope agents (capability/application + FAQ/company) plus a deflection
path, the grounding gate that never ships an uncited or invented-spec claim, and an interactive
`agentkit chat` CLI. The remaining four agents, handoff/lead capture, email and the widget are
milestone 5b+. The runtime LLM is the self-hosted `LLM_BASE_URL` (qwen3.5:9b via Ollama) —
never the extractor endpoint, never external (PRD-N-002). `ops.tool_call` logging begins here
(LLD-TOOL preamble).

## 1. What was implemented

### Migration 0004 — full `ops.*` schema (LLD-DB-04, data-model §3)
- `migrations/versions/0004_ops.py` creates all ten tables exactly per data-model §3:
  `client`, `prompt_version`, `session`, `turn`, `message`, `agent_invocation`, `tool_call`,
  `citation`, `lead`, `handoff_dispatch`. `lead` / `handoff_dispatch` are created now but written
  only from M6. `ops.message` carries `turn_id` FK, `seq_in_turn`, `kind ∈ {text,
  document_card, status, form}` (CHECK). A partial unique index enforces one active
  `prompt_version` per `(client, agent)`. Working `downgrade`; round-trip covered.

### Runtime LLM seam (LLD-RT, PRD-N-002)
- `runtime/llm.py::RuntimeClient` targets `LLM_BASE_URL` / `LLM_MODEL` and **reuses** the
  extractor's `build_request_kwargs` + `call_json` (strict `json_schema` response_format,
  retry-on-invalid, sampling knobs sent only when set). It never falls back to the extractor
  endpoint. `config.py` gained `llm_temperature/reasoning_effort/max_completion_tokens`.
  Verified: qwen3.5:9b honours the strict `json_schema` and returns exactly the triage shape.

### Prompt versioning (scope 2)
- `runtime/prompts.py`: `agentkit prompt load <client>` upserts `ops.client` (from
  `config.yaml` + the active release) and loads a fixed runtime prompt manifest
  (`triage`, `application_discovery`, `faq_company`, `deflect`) into `ops.prompt_version` as
  new inactive versions. `agentkit prompt activate <id>` flips a version active **gated by the
  golden suite** (`activate_prompt_gated`, a SAVEPOINT + `run_eval` — any fact/retrieval
  failure rolls the activation back), mirroring `release activate`. The runtime reads the
  ACTIVE prompt from the DB and falls back to the file only when none is active, logged loudly.
- New prompt bodies under `clients/jyotech/prompts/`.

### Orchestrator + triage + grounding (scope 3, 5)
- `runtime/orchestrator.py`: a thin in-memory LangGraph `StateGraph`
  (`triage → route → agent → ground → respond`). `runtime/triage.py` (LLD-RT-04) classifies
  `{division, intent, language, in_scope, pii_present, confidence}`; confidence < 0.6 →
  clarify. `runtime/tools_registry.py` runs the read-only tools, times them, and logs
  `ops.tool_call` (each call wrapped in a SAVEPOINT so an infra failure — e.g. no vector
  table — never poisons the turn). `runtime/grounding.py` is the gate (see §2). `runtime/ops.py`
  holds insert-only writers + `rebuild_context` for `--session` resume.
  **Graceful degradation:** if the runtime LLM returns invalid JSON 3× (`ExtractionSkipped`),
  triage falls back to a low-confidence default (→ clarify) and an agent falls back to a clarify
  reply — the turn is still recorded, never crashes (found during the live run; test
  `test_llm_hiccup_degrades.py`). The slot-extraction compose guidance is kept **out** of the
  slot-extraction prompt (so it reliably returns slot JSON) and passed to the compose step in
  code instead.

### Agents (scope 4 — only these two + deflect)
- `agents/application_discovery.py` (LLD-AG-01): slot order gas → capacity(+unit) →
  discharge_p; asks exactly one missing slot, never invents a value; complete slots →
  `match_capability` (alias map + null-lubricated caveat). It answers from the **top match's own
  published row fields**, then retrieves `search_documents` scoped to **that family only** for
  supporting prose (the catalogue chunk); the top match's own `near_edge` → "confirm with
  engineers"; no in-range match → honest close naming non-comparable relatives in one line +
  handoff-style offer (a **stub** — no `ops.lead` row in 5a). (This is the post-live-run shape;
  see §6 for the before/after.)
- `agents/faq_company.py` (LLD-AG-06 / -03-lite): `get_company_fact` for the implied kind +
  `search_documents` fallback → grounded answer citing locator + url.
- `agents/deflect.py`: polite scope statement for out-of-scope and the not-yet-built 5b
  intents; still recorded in `ops`.

### CLI (scope 6)
- `agentkit prompt load|activate` and `agentkit chat jyotech [--session <id>] [--show-trace]`
  — an interactive loop against the self-hosted runtime LLM; `--show-trace` prints the turn's
  ops rows (turn, messages, agent invocation, tool calls, citations) in data-model §4 style.

## 2. Deviations & notes (rule 6 — flag, do not edit LLD.md)

1. **LangGraph Postgres checkpointer deferred (LLD-RT-02).** 5a uses a thin in-memory
   `StateGraph`; all persistence is explicit `ops.*` inserts in the nodes (write-before-emit).
   The Postgres checkpointer (a new dependency) is deferred to the widget/API milestone where
   async resumption makes it necessary. **Rider:** `chat --session <id>` resume still works in
   5a by rebuilding context from the `ops` rows (`rebuild_context`, tested) — the deferral
   costs no functionality. **Rider:** the `ops` rows remain the **single source of truth** even
   after a checkpointer is later added; 5b/7 must not invert this (the checkpointer, if added,
   is a cache over ops, never the authority).

2. **Grounding gate = citation-backed + numeric guard (LLD-RT-05).** LLD-RT-05 describes
   per-claim splitting; without a second NLI pass that is unreliable, so 5a ships a two-part
   gate: (a) an `answer` must carry ≥1 citation resolving to a real tool-result of this turn,
   **and (b) a numeric guard** — every *spec number* in the answer (a figure adjacent to a
   capacity/pressure/measure unit) must appear in this turn's tool results, tool args or the
   visitor's own message. An unsourced spec number is treated exactly like an uncited answer:
   strip → the honest "not in our published material" + handoff-offer fallback. This catches
   the worst failure mode — an invented spec beside a genuine citation — at trivial cost.
   Per-claim NLI splitting stays a deferred LLD-RT-05 refinement. Fold both into LLD-RT-05 via
   the clarification path at the next doc pass.

3. **Write-before-emit realised as an atomic per-turn flush (LLD-RT-06).** The CLI computes a
   whole turn in memory and flushes it (`persist_turn`) in FK order at `respond`, keeping
   `ops.*` append-only (no UPDATE/DELETE on conversation rows — tested). The token-level SSE
   ordering of LLD-RT-06 is an API-milestone concern; the atomic flush is the CLI's honest
   realisation of "written before emitted".

4. **pytest tests mechanism on seed data; the golden suite pins real ids on the live release.**
   The hydrogen-turn pytest (on `seeded_conn`) asserts the §4 ops-row *shape* and that each
   citation resolves to a real tool-result (demo ids `cap.002` / `fam.process_recip`). The
   exact live id `cap.doc_jyotech_catalog_process_s005.0` is pinned by the golden suite
   (`cap-hydrogen-process` at the fact layer; the 5b e2e layer on every activation) and by the
   live `chat --show-trace` acceptance. Future milestones must not pin release data in unit
   tests.

5. **`ops.client` / `ops.prompt_version` are loader-managed config.** The append-only rule
   constrains the runtime's *conversation* writes; the `prompt load` tooling upserts the client
   row and `prompt activate` flips the designed `is_active` status column.

6. **5a routing of product_question / after_sales / commercial.** These have no agent until 5b,
   so triage routes them to the deflect/holding path (outcome `deflected`), distinct from true
   out-of-scope (`declined_oos`). No new intent was invented; only the 5a routing target.

7. **`ops.agent_invocation` records the `prompt_id` actually used** (audit trail for prompt
   changes) — a small strengthening within the data-model §3.4 shape, not a schema change.

## 3. Live run status (active release `r2026.08.2`, Ollama qwen3.5:9b + bge-m3)

All four acceptance conversations ran through the live `run_turn` path (identical to
`agentkit chat`), each in its own session; all four are recorded in `ops` (verified by a
per-session count of turns/messages/invocations/tool_calls/citations).

| case | message | outcome | route | grounding | notes |
|---|---|---|---|---|---|
| (a) | §4 hydrogen duty | `answered` | application_discovery | full | places the duty in **Process Compressors (Reciprocating)**, states its own published limits (25,000 Nm³/hr / 1,000 barg), **no near-edge claim**; cites `cap.doc_jyotech_catalog_process_s005.0` + the PROCESS catalogue chunk (§PROCESS COMPRESSORS (RECIP.)). This is the state **after** the §6 fixes; before/after quoted there. |
| (b) | "I need something for hydrogen" | `asked_slot` | application_discovery | — | asked exactly one slot ("required flow rate?"), no tool call — never guessed the duty. |
| (c) | "Are you ISO certified?" | `answered` | faq_company | full | "ISO 9001:2015, ISO 14001:2015, ISO 45001:2018", cites `cf.doc_about_s000.5` (locator §WHO WE ARE) + more. |
| (d) | "What's the weather in Mumbai?" | `declined_oos` | deflect | — | polite scope statement; no tool calls, no citations. |
| (e) | "How much does the MCH-16 cost?" | handoff/deflect | — | — | price never invented — routed away, no fabricated figure (PRD grounding guard). |
| (f) | hydrogen at 30,000 Nm³/hr (above the 25,000 published max) | held | application_discovery | — | break-in attempt held: no in-range match fabricated; honest no-confirmed-range + engineer offer. |
| — | `chat --session <id>` re-open | — | — | — | prior turns rebuilt from `ops` rows (checkpointer-free resume). |

Observations / honest caveats:
- **Grounding held in every case** — nothing ungrounded shipped; every factual answer carried
  citations with locator+url. This is the milestone's key guarantee.
- **Grounding held in every case** and the mechanism (route → match → ground → cite → §4 trace)
  was fully proven. Two follow-on issues surfaced on the hydrogen turn and were **fixed** — see
  §6.
- **Robustness bug found + fixed during the live run.** An `ExtractionSkipped` (runtime LLM
  returning invalid JSON 3×) crashed the whole turn. Now triage degrades to a low-confidence
  clarify and an agent degrades to a clarify reply; the turn is still recorded, never crashes
  (`test_llm_hiccup_degrades.py`).
- **Latency + hardware.** On the dev Mac (Metal) qwen3.5:9b was slow (turn (a) ~17 min). Pointing
  `LLM_BASE_URL`/`EMBED_BASE_URL` at a remote CUDA host running **qwen3.8:27b** (config only, no
  code change — PRD-N-002) cut turns to seconds (a: 21–29 s, b: 4 s, c: 7 s, d: 3 s) and the full
  `pytest -q` still passed against that endpoint. Latency is a PRD-N-001 concern, not a 5a gate.

## 4. Exit criteria status

- `pytest -q` green (full suite). ✔
- `agentkit db upgrade` applies `0004`; `downgrade base → upgrade head` clean. ✔
- `agentkit prompt load jyotech` + `activate` work with the golden gate (activation refused on
  a failing suite; a passing suite activates). ✔
- Live `agentkit chat jyotech --show-trace`: see §3.

## 5. Traceability

`docs/TRACEABILITY.md` Code/Tests cells updated for the runtime rows:
PRD-F-001 (triage), PRD-F-002 (application slots), PRD-F-008 (citations/grounding runtime),
PRD-F-009 (ungrounded removal), PRD-F-012 (Hinglish — routing + language tag; full retrieval
translation later), PRD-F-013 (deflect), PRD-N-005 (prompt-activate gate). LLD-DB-04, LLD-RT-*,
LLD-AG-01/-06 now have code + tests.

## 6. Live-run findings + fixes (hydrogen turn, ops turn ab357000 + the 27b rerun)

The live hydrogen turn kept anchoring on the hydrogen-*fuelling* page's "700 Nm³/hr" figure and
framing the 3,000 Nm³/hr duty as out-of-range — through prompt tweaks and a bigger model
(qwen3.8:27b). Two confirmed root causes, three fixes (tests-first, one commit each):

1. **`match_capability` over-returned non-comparable rows** (LLD-TOOL-01). Rows whose
   capacity unit was null (`…_fueling_system_s000.1`) or Kg/hr came back as *matches* with
   `headroom None` under a single global `near_edge`. New shape:
   `{matches, non_comparable_candidates, any_near_edge}` — `matches` = every requested comparable
   filter passed, ranked by fit, each with its own published limits + per-dimension headroom +
   own `near_edge`; a row that *fails* a comparable filter (700 Nm³/hr < 3000) is excluded
   entirely; a row a filter *cannot evaluate* is a candidate with a reason, never a match.
   Regression `test_hydrogen_3000_regression_matches_vs_non_comparable`. (LLD-TOOL-01 result-shape
   change flagged via rule 6 for the doc pass.)

2. **The agent narrated from chunks, and retrieved across all matched families** (LLD-AG-01).
   Now it answers from the **top match's own row fields**, and calls
   `search_documents` *after* selecting the top match, **filtered to that family only** with a
   family-name+gas query — so the PROCESS catalogue chunk surfaces (verified: top hit
   `doc.jyotech_catalog_process`), not the fuelling page. Near-edge caveat only on the top match's
   own `near_edge`; no-match offers engineer review and names non-comparable relatives in one
   line. Golden `cap-hydrogen-process` retrieval block updated to mirror the family-filtered query.

3. **Customer-facing prompt** (activated through the golden gate). Plain family names
   only (no `fam.*`/`cap.*`), no slot vocabulary, machines described by their own published
   fields, non-comparable relatives ≤1 sentence, and the recommended family's published limits
   stated explicitly.

**Before / after** of the §4 hydrogen answer (3,000 Nm³/hr, 20→350 barg, oil-free):

- **Before** (pre-fix): *"…that capacity exceeds our published diaphragm/hydrogen range (up to
  700 Nm³/hr). Your 3,000 Nm³/hr flow sits well outside that published capacity limit… I'd
  strongly recommend a direct engineering review."* — grounded and cited, but anchored on the
  hydrogen-**fuelling** page's 700 Nm³/hr figure and wrongly framed the duty as out-of-range.
- **After** (post-fix, qwen3.8:27b, 29 s): *"…comfortably within our Process Compressors
  (Reciprocating) range. That family is published up to 25,000 Nm³/hr and 1,000 barg, so your
  duty sits well inside the envelope and I can recommend it with confidence."* — Type / oil-free
  / API-618 / gases listed; the kg/hr lines flagged as non-comparable with a one-line engineer
  cross-check offer.

Citations after the fix: `capability cap.doc_jyotech_catalog_process_s005.0` + `chunk
ch.jyotech_catalog_process.0003` (§PROCESS COMPRESSORS (RECIP.), **not** the fuelling page);
grounding full (4/4); **no near-edge claim**. All acceptance criteria met.

Related deferrals still standing (unchanged by these fixes, see §2): the LangGraph **Postgres
checkpointer** (widget/API milestone; `ops` rows stay the source of truth) and the **per-claim
grounding** refinement (5a ships citation-backed + numeric guard). And the standing division of
labour — **pytest tests the mechanism on the demo seed** (`cap.002` / `fam.process_recip`),
while the **golden suite pins the real live ids** (`cap.doc_jyotech_catalog_process_s005.0`) on
the active release — is why fix 1's regression is written on constructed seed rows, not live ids.
