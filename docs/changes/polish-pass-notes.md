---
title: Post-merge polish pass — stage events, Markdown formatting, source-voice
date: 2026-09-01
author: Claude Code (paired with arnab.sharma)
type: implementation note (voice/formatting + additive plumbing; no PRD/HLD/LLD design change)
lld_items: [LLD-RT-02, LLD-RT-05, LLD-AG-02, LLD-AG-03, LLD-AG-04, LLD-AG-05, LLD-EVAL-02, LLD-EVAL-03]
prd_row: PRD-F-004, PRD-F-005, PRD-F-006, PRD-F-007, PRD-F-008, PRD-F-009
branch: post-merge-polish
---

# Post-merge polish pass

A small three-part polish on top of the merged `milestone-5b` + `gradio-dev-ui` work. No design
change — this is voice/formatting on the prompt layer plus one additive, inert-by-default runtime
seam. Documents remain the source of truth; nothing in HLD/LLD required editing.

## Part A — real stage events (additive code)

The Gradio dev harness previously showed a single generic "working on your answer… (Ns)" spinner
because no real per-node signal existed. This pass adds an **optional, read-only** progress seam:

- `Ctx` gains `on_stage: Callable[[str], None] | None = None` (`runtime/orchestrator.py`).
- Each orchestrator node calls it once at the top with a short human label, via a tiny `_stage()`
  helper — in graph order:
  - triage → **"Understanding your question"**
  - route → **"Finding the right specialist"**
  - agent → **"Looking into it"**
  - ground → **"Checking our sources"**
  - respond → **"Finishing up"**
- The CLI passes nothing → `on_stage` is `None` → the callback is inert and the turn is unchanged.
- The Gradio harness (`apps/dev_ui.py`) creates a per-turn `queue.Queue`, wraps `turn_runner` to
  set `on_stage=q.put`, and passes a **reusable** queue-iterator (`_QueueStages`) as the harness's
  existing `stage_source`. `_QueueStages.__next__` pops one label or raises `StopIteration` when the
  queue is momentarily empty (the harness then shows its generic elapsed-seconds line for that
  poll). A plain generator would be wrong here — it dies permanently after the first empty read;
  the class stays usable across polls.

Note: the graph has a single generic `agent` dispatch node (it routes internally via `wf.route`),
so one static label there is correct — there is no per-agent node to label.

Follow-up (typewriter pacing): the submit handler already yielded per TOKEN, but
`harness.stream_turn`'s typewriter loop emitted with no delay, so the whole answer rendered in one
paint. Added a `token_delay` (default 0.03 s, slept after each TOKEN) so Gradio repaints
word-by-word; tests pass `token_delay=0` to stay fast (they assert ordering, not timing).

**Tests:** `tests/runtime/test_on_stage_labels.py` (labels recorded in node order; default-None is
inert) and two additions to `tests/apps/test_dev_ui_adapter.py` (the queue iterator's
reusable-after-empty behaviour; the `stage_source` STATUS branch, previously untested).

## Part B — Markdown formatting (prompt layer, all six customer agents)

Presentation rules live in the shared `clients/jyotech/prompts/_persona.md` (prepended to the six
customer agents), so one edit propagates. Added rules:

- **Bold** for product/family display names.
- A **small Markdown table** only for **three or more** variants or a per-type spec set (the
  `get_product` variants case; per-type tables like the diaphragm/hydraulic/hybrid hydrogen types).
- **Prose** for everything else — no headings, no bullet lists in a short answer.
- A load-bearing constraint: **keep each figure and its unit together in one cell / one bold span**
  (`| 25,000 Nm³/hr |`, `**25,000 Nm³/hr**`) and never split them. The grounding numeric guard's
  `_SPEC_RE` only tolerates whitespace between a number and its unit; splitting them across a `|`
  or around `**` would make the spec figure invisible to the guard.

Exemplars updated to show the shape: `product_advisor.md` (a 3-row electric-variant table),
`application_discovery.md` (a per-type diaphragm/hydraulic/hybrid envelope table), with display
names bolded.

**Guard tests (prove `*`/`|` don't break the numeric machinery):**
`tests/runtime/test_ungrounded_claims_removed.py` — `spec_numbers`/`all_numbers`/redaction stay
correct with bold and table markup. `tests/eval/test_runner.py` — `text_contains`/`normalize_digits`
(the eval matcher) is blind to the surrounding `*`/`|`, and `must_not_contain` still catches a
forbidden needle inside a bold span or table cell.

## Part C — source-voice: no document names in prose (prompt layer, same bump)

The answer text must speak **as Jyotech** and never name a source document, catalogue, page, or
"our published material"; provenance rides only in the structured `citations`, which the UI renders
as a separate Sources list. The rule is stated once in `_persona.md`, phrased by **question type**
so it is correct for all six agents:

> Never name a source in the answer text … The only time you name a document is when the customer
> is explicitly asking for the documentation itself (a catalogue/datasheet/certificate to view or
> download) or asks which source a figure came from.

Real leaks fixed (the strings quoted in the task brief were illustrative; these are the actual
in-prompt leaks):
- `product_advisor.md` and `application_discovery.md`: removed "— happy to share the catalogue
  page" from the challenge exemplars; the correct published figure is restated confidently and the
  source stays in the citations.
- `faq_company.md`: the "Cite the source: include the locator and document URL" instruction now
  routes provenance to the `citations` array and forbids naming the catalogue/page; the API-618
  example is stated as Jyotech's own voice.
- `documents_compliance.md` (narrow fix — see decisions): a compliance/standard answer keeps
  provenance in the citations and does not name the catalogue; the document-**download** exemplar
  (naming the Fire/Rescue catalogue) is kept, because that is the customer asking for the document
  itself.
- `_persona.md` Voice/Honesty lines reworded off the "published material" phrasing.

`after_sales_intake.md` and `commercial_routing.md` had no per-file leaks and receive the rule via
the shared persona bump only.

## Goldens

`clients/jyotech/golden/questions.yaml` — added `must_not_contain: ["Source:", "catalog",
"catalogue", "page)"]` to three questions that do **not** ask about documents:
`co-certifications`, `faq-api618` (appended to its existing absence guards), and `prod-mch16-specs`.
The `docs-*` questions were deliberately left untouched (they legitimately discuss catalogues).

## Decisions taken

- **Variants: table at 3+, prose at 2.** `product_advisor`'s multi-variant rule keeps the
  one-sentence prose siblings form for one or two variants and switches to a small table only at
  three or more — minimal disruption to the existing voice.
- **Narrow `documents_compliance` fix.** Although Part C's rule is scoped to the other five agents,
  `faq-api618` routes to `documents_compliance`, and a machine-compliance question is not a request
  for a document — so its provenance wording was fixed to keep the catalogue out of the prose while
  preserving catalogue-naming for genuine document-download requests. Without this the new
  `must_not_contain` on `faq-api618` could not hold.
- **Stage labels.** Chose short, customer-neutral labels (above) over internal node names; they are
  honest per-node signals, not a fabricated multi-stage animation.

## Gate strategy and result

`prompt load` bumps every runtime agent, but only the six persona agents changed. Rather than run
six full three-layer gate cycles, the **documented bring-up rule** (`prompt activate --layers
fact,retrieval`, then `agentkit eval run` proves all three) was applied:

1. Activated five of the six with `--layers fact,retrieval` (fast; these layers don't exercise
   prompt text): application_discovery, product_advisor, after_sales_intake, commercial_routing,
   faq_company. All passed.
2. Activated the **last** one (`documents_compliance`) through the full `fact,retrieval,e2e` gate —
   by that point all six new prompts are active, so its e2e run exercises the complete final set.
3. Ran one standalone `agentkit eval run jyotech` (all layers) as the definitive proof.

The intermediate bring-up activations are partial by design (per the bring-up rule); the **end
state is e2e-green**.

**Result — e2e-green.**

- Five bring-up activations (`--layers fact,retrieval`): all passed.
- `documents_compliance` through the full gate: `Golden suite passed (fact+retrieval+e2e)` — the
  e2e layer ran the whole 52-question suite against all six new prompts active together.
- Standalone `agentkit eval run jyotech` (all layers): **RESULT: PASS** —
  `fact na=10 pass=42`, `retrieval na=46 pass=6`, `e2e pass=52` (runtime ~1313s). The three
  questions carrying the new `must_not_contain` source-voice guards — `co-certifications`,
  `faq-api618`, `prod-mch16-specs` — and `e2e-hinglish-hydrogen` all pass.

**Live recheck** (the three answers that motivated Part C, run through the dev UI's own
`turn_runner` + `build_sources` seams against the live runtime):

- *"Which ISO certifications does Jyotech hold?"* → prose lists the three ISO certs and names no
  document; Sources panel carries the company-fact + catalogue rows.
- *"Are your machines API-618 compliant?"* (→ documents_compliance) → *"…designed and manufactured
  based on maker standard, and API-618 or equivalent global standard…"* — spoken as Jyotech, no
  catalogue named; **Process Compressors** / **Oxygen Compressors** bolded; the PROCESS-catalogue
  chunk (with URL) is in Sources.
- *"Kya aap hydrogen compressor banate ho?"* (Hinglish → product_advisor) → Hinglish prose, no
  document named, **Hydrogen Compressors** / **Hydrogen Fuelling Systems** bolded; families + a
  source chunk (with URL) in Sources.

All three: prose document-name-free (Hinglish included), Sources panel intact.

## Files changed

- Runtime: `src/agentkit/runtime/orchestrator.py` (Ctx + 5 node calls + `_stage`).
- Harness: `apps/dev_ui.py` (`_QueueStages`, per-turn queue wiring).
- Prompts: `clients/jyotech/prompts/_persona.md`, `product_advisor.md`,
  `application_discovery.md`, `faq_company.md`, `documents_compliance.md`.
- Goldens: `clients/jyotech/golden/questions.yaml` (3 questions).
- Tests: `tests/runtime/test_on_stage_labels.py` (new),
  `tests/apps/test_dev_ui_adapter.py`, `tests/runtime/test_ungrounded_claims_removed.py`,
  `tests/eval/test_runner.py`.
