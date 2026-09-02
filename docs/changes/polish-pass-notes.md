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

Follow-up (sticky stage trail): `_status()` re-derived the label from `next(src)` every poll, so an
empty poll reverted to the generic line and each label flashed for ~one poll then vanished. Labels
are now **sticky** and accumulate into a trail — completed stages prefixed `✓`, the current one last
with the ticking timer (`✓ Understanding your question · ✓ Finding the right specialist · Looking
into it… (8s)`); the generic line shows only before the first label. Adapter tests cover the sticky
hold across empty polls and the completed/current trail transition.

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

## Follow-up — documents_compliance: one card per referenced document (rule-6 flag)

**Bug:** a generic catalogue ask ("what product documents do you have?") produced prose naming
**both** catalogues (PROCESS and F&S) and promising "cards below" (plural), but only **one**
`document_card` was emitted — the 5b design carded the single top-ranked document, which is right
for a specific ask and wrong for a generic one.

**Fix (agent/tool composition within LLD-AG-03 — no LLD.md edit, flagged here for the next doc
pass):**
- `runtime/grounding.py` gains `used_documents(chunks, answer_text, cap=None)` — the multi-document
  generalisation of `_best_chunk`: the distinct documents (by `doc_id`, retrieval order) whose
  chunks the answer actually used (overlap ≥ 2 distinctive tokens; falls back to the single best
  chunk, never zero). The `search_documents` citation derivation now cites **each** used document,
  not just the best chunk — so a generic catalogue answer cites every catalogue it drew on (a
  specific answer still cites exactly one). Extra citations are subset-safe for goldens.
- `runtime/agents/documents_compliance.py` emits one `document_card` **per distinct downloadable
  document the answer references** (`used_documents`, filtered to file URLs — `.pdf` etc. — so a
  retrieved HTML listing page never becomes a bogus download; deduped, retrieval order, capped at
  3). Retrieval `k` raised to 10 so a generic ask surfaces every catalogue, not just the top hit.
  The compose instruction ties prose to cards (name a catalogue ⇒ a card; singular/plural to match
  the count). A specific single-document ask still yields exactly one card.

**Result (live):** *"What catalogues do you have for download?"* → two cards (F&S + PROCESS), prose
names both with "cards below", both catalogue documents cited. *"Do you have a fire equipment
catalogue?"* → one card, singular prose. Cards remain on the grounded answer path (`extra_messages`,
dropped with a failed draft).

**Tests:** `tests/runtime/test_citations.py` — `used_documents` (each referenced doc in order;
dedupe + cap; single-reference → one; no-overlap fallback; both docs cited). `tests/agents/
test_documents.py::test_generic_catalogue_ask_serves_one_card_per_document` — a two-catalogue turn
yields two cards and cites both. **Golden:** `docs-both-catalogues` — "What catalogues do you have
for download?", `expected_source_ids` includes **both** `doc.jyotech_catalog_process` and
`doc.jyotech_catalog_f_s`.

**Gate note (pre-existing flaky golden — now fixed, see below):** the confirming
`agentkit eval run jyotech` reported `docs-both-catalogues` **pass** and every source-voice golden
pass, with one failure — `cap-hydrogen-fuelling` `missing sources ['fam.hydrogen_fuelling_system']`
— which was a pre-existing flake unrelated to the multi-card change (the family is derived from the
`get_product`/`list_products` citation, not the `search_documents` branch this change touched). It
is fixed in the follow-up below.

## Follow-up — product_advisor: always ground the product/family source (fixes cap-hydrogen-fuelling)

**Root cause:** product_advisor already force-cites its structured lookup (`structured_rec.tr_id`,
mirroring application_discovery's match force-cite) — but only when one *ran*. The lookup was an
`if get_product(...) / elif list_products(division)` chain: when the parse extracted a family name
`get_product` **cannot resolve** (e.g. "Hydrogen Fuelling Systems" — a family with no product rows;
`get_product` returns `[]`), the `if` branch was taken, `structured_rec` stayed `None`, and the
`elif` listing fallback was **skipped**. The answer then carried no structured product/family
source, so the family id was grounded only if the compose LLM happened to name it — which it
intermittently omitted. (When the parse returned null, the `list_products` path ran and the family
was grounded, which is why the question usually passed.)

**Fix (code-only, no prompt/gate change):** the `elif` became a separate fallback —
`if structured_rec is None and division is not None: list_products(division)` — so an unresolved
name (or a null name) always yields a force-cited listing record. `list_products("industrial")`
carries `fam.hydrogen_fuelling_system`, so the family is now grounded on every path.

**Tests** (`tests/agents/test_product_advisor.py`): a get_product-backed answer whose compose cites
only the chunk still emits the **product + family** citations (the force-cite guarantee); an
unresolvable name falls back to `list_products` and still grounds a **family** citation.

## Goldens

`clients/jyotech/golden/questions.yaml` — added `must_not_contain: ["Source:", "catalog",
"catalogue", "page)"]` to three questions that do **not** ask about documents:
`co-certifications`, `faq-api618` (appended to its existing absence guards), and `prod-mch16-specs`.
The `docs-*` questions were deliberately left untouched (they legitimately discuss catalogues).
Added `docs-both-catalogues` (see the multi-card follow-up above).

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
