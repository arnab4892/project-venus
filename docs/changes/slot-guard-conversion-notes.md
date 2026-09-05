# Slot mis-extraction guard + tool-computed unit conversion — working notes

Branch `slot-guard-conversion` off `main` (post-PR #11). Prompt + small structural code changes; no
LLD/HLD/PRD edits (rule 6). Fixes a live miss (turn `8fdc353f`).

## The miss (trace-verified)
*"Natural gas, 50000 SCMD, wellhead application, gas engine driven, 120 bar"* → the
`application_discovery` slot LLM produced `lubricated=false` from **"gas engine driven"** (a
prime-mover phrase, not a lubrication statement). The Natural Gas family is published *Lubricated*, so
the false oil-free filter excluded it (correct comparable-filter semantics, wrong input); the
Nm³/hr-published Process family became the only match; the compose then did **SCMD→Nm³/hr arithmetic
in prose, 10× wrong** (`20,833`; correct ≈ `2,083`), and the numeric guard stripped the turn to
`outcome=fallback` (`unsourced_numbers:[1.0, 20800.0, 20833.0]`, `grounded=0`). The gate and tool
behaved correctly — the bugs were the **parse** and the **prose arithmetic**.

Probabilistic: this exact question is the golden `cap-wellhead-natgas`, which passed the 58/58
definitive gate but failed a single-shot browser run — a sampled e2e pass cannot certify the
mis-extraction's absence, so the fix is a **deterministic code guard**, not a new golden.

## What changed
- **Prompt** `application_discovery.md`: `lubricated` is set only by an explicit lubrication statement;
  prime-mover / driver mentions leave it null (verbatim example `gas engine driven → lubricated: null`);
  same rule for `standard` (only a named code). Plus a unit-reconciliation exemplar (Fix 3).
- **Code guard** `application_discovery.py` (`_message_states_lubrication` / `_message_states_standard`
  / `_guard_inferred_filters`): before `match_capability`, null an inferred `lubricated`/`standard` the
  visitor never stated. Token sets — lubrication (case-insensitive): `oil-free`, `oil free`, `oilless`,
  `oil-less`, `non-lube`, `lube`, the `lubric` stem, **never bare "oil"**; Devanagari `ऑयल-फ्री` /
  `ऑइल-फ्री` / `ऑयल फ्री` / `लुब्रिकेटेड` / `नॉन-लुब्रिकेटेड` / `तेल-मुक्त`. Standard (Latin only): acronyms
  `api|iso|asme|atex|ped|nfpa|eiga|din|ansi|iec` case-insensitive, plus `EN|IS|BS|BIS` + digit
  **case-sensitive UPPERCASE** (so "capacity **is** 3000" never validates a filter). Scans the current
  message **plus prior user turns**. Every strip is `logger.info`-traced (slot + discarded value),
  fail-open.
- **Persona** `_persona.md` (all six agents): never do arithmetic or unit conversion yourself — present
  a tool-provided converted figure, never derive one.
- **Tool** `match_capability.py`: matches additively carry `converted_capacity {value, unit, from}` when
  the family is reached by unit conversion (`not same_unit`); the value is the already-computed
  `conv_capacity`. `render_tool_context` surfaces it so the compose reconciles units from a sourced
  figure. No matching / ranking / exclusion change; `fact` goldens unaffected.
- **Golden** `cap-wellhead-natgas`: `must_not_contain` tightened to `["25000", "20833", "20,833"]`
  (guards the observed wrong 10× conversion). No new golden — count stays 58.

## Why the `standard` guard too (not just `lubricated`)
`standard` is the same optional slot, inferred by the same LLM (the relaxation-retry comment names
"diaphragm compressor → API-618"). The relaxation retry only fires on an **empty** match — a false
filter that yields a **wrong-but-nonempty** match (NULL rows survive) is never caught by it. The
pre-match token guard is the only thing that closes the wrong-match path.

## Accepted limitation (user-turns-only scanning)
A visitor *affirming an option the BOT named* ("we build these oil-free" → "yes, that one") can have
the token only in the **assistant** turn, and the guard nulls the slot. Accepted because: the agent
never asks about lubrication (optional, never blocking), so the customer must never type the word
across the whole conversation for this to occur; history scanning means one mention *anywhere in the
user turns* locks the preference in; the failure direction is safe (`null` = unspecified / both
offered, caveated, recoverable in one turn); and scanning assistant turns would validate nearly any
hallucinated filter. The strip-event traces let us address it with real evidence if it ever occurs in
live traffic, rather than a speculative heuristic now.

## Rule-6 flags (for the next doc pass)
- **LLD-AG-01**: pre-match token guards null an over-inferred `lubricated`/`standard` when no
  lubrication/standard token appears in the visitor's turns (closes the wrong-but-nonempty-match path
  the empty-match-only relaxation retry misses); token sets as above; strip events traced (fail-open).
- **LLD-TOOL-01**: `match_capability` matches additively carry `converted_capacity {value, unit, from}`
  when reached by unit conversion (LLD-EXT-09); result-shape only, no matching/ranking/exclusion change.
- **Persona / LLD-RT-05**: no-arithmetic honesty rule — figures are presented from tool results (or the
  visitor's message), never derived; the compose never converts units (the tool does, Fix 3).

## M7 follow-up (decided, not built here)
Generalise the **LLD-EXT-04 verbatim-evidence-gate** to runtime slot extraction: every non-null slot
returns the verbatim message phrase it came from, code verifies the phrase is in the message,
unverifiable slots are nulled; applies to `application_discovery` and `after_sales_intake`. The token
guard stays even then (evidence-quoting proves *presence*, not *semantic support* — "gas engine
driven" quoted as evidence for `lubricated` would still be wrong). **The generic mechanism must verify
evidence across scripts (Latin + Devanagari), or it inherits the same bias.**

## Gate & verification
`pytest -q` green (341). Prompt changes touch `_persona.md` (⇒ all six persona-agents re-version) +
`application_discovery.md` → batch bring-up under thinking-on, full three-layer gate over the 58-suite,
standalone all-layer `eval run` as the definitive PASS. Live acceptance: the exact quoted question run
3–5× through `agentkit chat` — a Natural Gas (SCMD) answer every time, no derived figures, no fallback.
