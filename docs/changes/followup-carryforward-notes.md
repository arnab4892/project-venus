# Follow-up duty carry-forward + slot-message hygiene + answer backstop — working notes

Branch `followup-carryforward` off `main` (post-PR #12). **Code-only** — no prompt files changed,
so no prompt re-activation; the full three-layer gate runs via `agentkit eval run` before merge.
Fixes a live multi-turn miss (turn `b43d99f6`).

## The miss (trace-verified)
Turn 1 ("hydrogen, 24000 SCMD, 20→350 bar, oil-free") answered. Turn 2 ("What about 80000 SCMD?")
made **0 tool calls**: the slot extraction did not carry gas/discharge_p/oil-free forward from
history, the agent took the ask_slot path, and its "question" was an answer-shaped recitation of the
prior turn's published figures — which the every-message numeric guard correctly stripped
(`stripped_unsourced_numbers [1000, 25000]`), shipping a mutilated *"published up to … and …"* to the
customer. The slot-extraction call thinks even under `LLM_DISABLE_THINKING=1` (the knob is
compose-only), so this is a **multi-turn design gap**, not a knob artifact.

## Fix 1 — deterministic slot carry-forward (the fix)
`application_discovery.run`: before deciding a required slot is missing, `_merge_followup` merges the
newly extracted slots over the session's **prior duty** — `_prior_duty` reads the args of the most
recent `match_capability` call in this session from the recorded tool calls (the authoritative prior
duty; within that turn it takes the first non-null per key so the stated duty wins over a relaxation
call). A non-null new value wins; a null inherits. Scoped to application_discovery, same session,
its own `match_capability` calls. `session_id` is threaded via `ToolRunner` (set in `orchestrator.n_agent`).
- **Full restatement** (all required slots stated anew) overrides everything and **inherits nothing**
  — including the optional filters, so a stale oil-free/standard from an earlier duty never rides onto
  a fresh one.
- **(a) Ambiguity is asked, never guessed:** when the visitor states a NEW flow but the **current
  message carries no flow-with-unit** (`_CAP_RE` doesn't match `latest_user`), the unit is cleared so
  the missing-slot check asks once — **regardless of a unit the slot LLM inferred from context**
  (given history, the LLM often fills `capacity_unit=SCMD` for a bare "what about 80000?"; the
  message-based check overrides that, since a bare number has no face value to take). Live: 2/3
  ask; the residual answers but honestly hedges ("I can't confirm where 80,000 SCMD lands — engineers
  check"), never a fabricated fit — LLM-extraction variance, not a wrong answer.
- **(b) Assumptions are spoken:** the answer path opens by restating the exact matched duty (flow +
  unit, pressure, gas, oil-free) via the compose `extra_instruction`, so an inherited value is visible
  and correctable in one turn. The duty figures are the visitor's flow (message) + the tool-args
  pressure, both in the numeric guard's allowed set, so the restatement passes the gate.
- The existing lubricated/standard **token guards run AFTER the merge**, scanning all user turns.

## Fix 2 — ask_slot message hygiene
On the ask_slot path, if the LLM's message contains any spec-number (`grounding.spec_numbers`) it is
DISCARDED and the deterministic `_SLOT_QUESTIONS` template is sent instead — a slot question never
carries figures, the customer never sees a redacted "up to … and …".

## Fix 3 — answer-must-name-family backstop (design note: denial-detection, not exact-name)
On the answer path, a "no results / tool failed" **denial composed despite a real match** (turn
`74d45612`: *"I'm not getting any results from our search … problem with the search tool"*) slips the
gate (the match is force-cited, no bad numbers). `_answer_denies_match` detects it; the agent
recomposes once forcing a real answer; if it still denies, it ships the honest per-language fallback
naming the family + offering engineers (never the tool-denial).

**Why denial-detection rather than the exact-display-name check the brief proposed:** an exact
family-display-name check false-fires on any legitimate paraphrase of the name (e.g. the compose
saying "process gas reciprocating range" instead of "Process Gas Compressor") — it broke 8 existing
gate/eval tests and would intercept, in production, both good paraphrased answers and numeric
fabrications the grounding gate already strips to the fallback. The denial detector targets the
actual observed failure (the tool-denial) precisely, is robust to paraphrase, and leaves the numeric
gate to handle fabrications. It fires on the "no results"-style answer despite a non-empty match, as
the brief requires.

## Supporting fix — converted_capacity rounded for presentation
`match_capability` now rounds the **presentation-only** `converted_capacity.value` to a whole unit
(80,000 SCMD → 3,333 Nm³/hr, not 3,333.33). Without it, the compose stated the reconciliation as
"≈3,333 Nm³/hr" but the allowed set held the unrounded 3,333.33, so the numeric guard stripped 3,333
and the answer fell back (observed 2/3 on the carry-forward turn). The internal `conv_capacity` used
for the envelope/headroom math is unchanged — only the display field is rounded; no
matching/ranking/exclusion change, `fact` goldens unaffected.

## Thinking-on vs thinking-off (live acceptance)
Under **thinking-on** (the certified config) the carry-forward turn is clean — 3/3 restate the merged
duty ("80,000 SCMD of hydrogen, 20 bar suction up to 350 bar, oil-free"), name the matched family, and
present the sourced converted figure, no fallback. Under **`LLM_DISABLE_THINKING=1`** the carry-forward
still reaches `match_capability` with the merged duty every time (the slot call thinks regardless), but
the thinking-off compose mis-reasons about the reconciliation (e.g. "3,333 above 25,000") or falls back
— the same pre-existing thinking-off English-compose degradation that keeps the knob **uncertified**,
not a defect in this fix. The partial pivot ("and what about oxygen?") carries forward oxygen + the
inherited 24,000 SCMD + 350 bar and honestly hands off (350 bar exceeds oxygen's 50 barg), echoing the
inherited duty so it's correctable.

## Edge recorded
The token guard runs after the merge and scans the passed `history`'s user turns. If a very long
conversation truncates `history` before the turn that stated oil-free, an **inherited** oil-free could
be stripped. Failure direction is safe (null = unspecified/both offered) and surfaced by the Fix 1b
restatement (correctable in one turn). A division change never inherits — it re-routes to a different
agent, and `_prior_duty` reads only application_discovery's own `match_capability` calls.

## Goldens / tests
No golden weakened; **no new e2e golden** — a multi-turn golden needs the `followup_turns[]` mechanism,
deferred to **M6**. This fix is the reason to build it: the first planned multi-turn golden is the
two-turn hydrogen follow-up (24000 SCMD → "What about 80000 SCMD?" → merged-duty answer). Unit/
integration tests (FakeLLM) added in `tests/agents/test_discovery_slots.py`: carry-forward, gas-override,
full-restatement (inherits nothing), unitless-follow-up (one question, no guessed-unit match),
ask_slot template swap, denial backstop (one recompose then fallback), guards-run-after-merge.

## Residual (recorded, not fixed here)
`_SLOT_QUESTIONS` is **English-only**. A hi/hinglish visitor gets the English slot template on the
Fix 2 swap path. Converting the slot templates to per-language (en/hi/hinglish) is a small follow-up,
grouped with the other fixed customer-facing strings (grounding fallback, clarify, no-match, price
handoff) that are already per-language.

## Live instability investigation (dev-UI turns during the eval window, 2026-09-05)

Four reported symptoms diagnosed from `ops.*` + Langfuse (`events_full`, since the Langfuse worker
had **not** materialised the `observations` table — 0 rows, 2 traces; the raw generations were only in
`events_full`). Server: **vLLM 0.28.0** at `103.48.50.161:8000`, model **`cyankiwi/Qwen3.8-27B-AWQ-INT4`**
(AWQ INT4 quantized). All four turns ran the new `application_discovery.25`.

**(a) "content filter" / (b) "…tool use(s) were not executed … malformed … Please retry." / (c)
"The server had an error …":** these are NOT API or harness failures. The compose call returned
**HTTP 200 with valid, schema-conforming JSON** — e.g. `fd73a36a` →
`{"message":"content filter","citations":[]}`, `70ca4ac2` →
`{"message":"The server had an error while processing your request. …"}`, `797de6d3` →
`{"message":"The following tool use(s) were not executed: search_documents … malformed. Please retry."}`.
Guided decoding **succeeded**; the **model filled the `message` string with memorised error-boilerplate**.
`match_capability` + `search_documents` ran fine (rows 1 / 2); the grounding gate passed the strings
(no figures, and the match is force-cited), so they shipped as `answered`. The generation ran at
**`temperature: 1, top_p: 1`** — because the runtime `llm_temperature` is **unset** (`None`), so no
temperature is sent and the SDK/vLLM default of `1` applies. High temperature on the INT4-quantized
model is the degeneracy driver; the confusing input for (a)/(c) — *"What if the discharge pressure is
24000 SCMD?"* (a **flow unit on a pressure**) — is the trigger. **(c) is model-generated content
shipped as an answer, not a harness crash** (a real crash would not persist a turn to `ops`).

These boilerplate strings are a **distinct class** from the "no results / tool-failed" denial the
Fix-3 backstop catches — they are not denial-shaped, so the backstop does not (and by design should
not) fire on them.

**(d) "Failed to advance FSM for request … Please file an issue":** a **separate** vLLM
**guided-decoding** failure, and a different failure mode from (a)–(c): an FSM failure produces
invalid/degenerate output → `call_json` retries → `ExtractionSkipped` → the orchestrator's clarify/
hiccup degrade — it does **not** yield the valid JSON seen above. The log line is on the **remote**
vLLM host (not locally accessible). "advance **FSM**" indicates an **FSM-based guided-decoding backend
(outlines / lm-format-enforcer)**, not xgrammar.

**Remedies (proposed, not applied here):**
- **Server-side for (d):** restart the vLLM server; confirm and prefer `--guided-decoding-backend
  xgrammar` (grammar-compiler, more robust than the FSM-based outlines/lm-format-enforcer on quantized
  models); consider that AWQ-INT4 quantization aggravates FSM instability; pin/upgrade vLLM if the FSM
  bug is version-specific. Operator action — the server is remote; confirm from its startup args + log.
- **For (a)–(c) (config / suite, not runtime code):** set a low compose **`LLM_TEMPERATURE` (0–0.2)** —
  this is the biggest lever against the boilerplate degeneracy (re-certify the gate after); and add the
  clarification golden below. The `application_discovery` code (carry-forward / merge / backstop) ran
  correctly on these turns — the failure is entirely in the compose model output.

## For the next suite extension (item 4)
**"What if the discharge pressure is 24000 SCMD?"** — a **flow unit (SCMD) on the pressure slot** — is a
good future **clarification golden**: the turn should ask a short clarifying question (a pressure is in
bar/barg, not SCMD), never match/answer on it. It also happens to be the input that most reliably
triggered the temp=1 compose degeneracy above. Add it when the suite gains the multi-turn / clarify
golden mechanism (M6, with `followup_turns[]`).

## Rule-6 flags (for the next doc pass)
- **LLD-AG-01**: follow-up carry-forward — merge newly extracted slots over the session's prior
  `match_capability` args (full restatement inherits nothing; unitless new flow is asked, not guessed;
  answer restates the merged duty); ask_slot messages carrying spec-numbers are replaced by the
  deterministic template; a "no results / tool failed" answer despite a match is recomposed once then
  falls back honestly (denial detection). `ToolRunner` gains `session_id`.
- **LLD-AG-01 (hardening)**: `_prior_duty` is fully **fail-open** — the read runs inside a SAVEPOINT
  (so a DB error rolls back only the read, never poisoning the turn's shared transaction) and any
  exception logs + returns `None`; the turn proceeds without inheritance (same posture as the sources
  resolver / tracing bolt-ons).
- **LLD-TOOL-01**: `converted_capacity.value` is rounded to a whole unit for presentation so the
  compose's reconciliation figure is sourced under the numeric guard (internal `conv_capacity`
  unchanged).
- **LLD-EVAL-01 / M6**: `followup_turns[]` multi-turn golden mechanism is now motivated; first golden =
  the two-turn hydrogen follow-up.
- **LLD-RT-07**: `_SLOT_QUESTIONS` per-language conversion residual.
