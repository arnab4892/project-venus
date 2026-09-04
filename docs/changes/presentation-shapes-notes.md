# Presentation shapes — working notes (branch `presentation-shapes`)

Two live-observed presentation problems, fixed in the compose/prompt layer with one authorised,
minimal runtime extension. No routing, gate-semantics, data or new-tool change.

## Problem 1 — division overviews orient instead of enumerate

A broad ask ("What do you offer in industrial compressors?", "Tell me about your fire & safety
range") routes to `product_advisor` (triage `product_question`), which runs `list_products(division)`
+ a division-scoped `search_documents`. It used to return a long family-by-family enumeration with
spec dumps. Now the answer groups the division into 3–5 natural clusters (by the family `category`
the listing already carries, or gas/use-case), one or two sentences each, **no per-family model
lists and no spec figures**, closing with an invitation to narrow down. Specifics appear only when
the visitor asks about something specific.

- Prompt only: `clients/jyotech/prompts/_persona.md` (Presentation: overview = clustered
  orientation, not enumeration/spec dump) + `clients/jyotech/prompts/product_advisor.md` (a
  "broad division / range ask" rule + per-language overview exemplars, language-tagged).
- Grounding unchanged: `list_products` stays force-cited, so the division's families are grounded;
  the orienting prose carries few/no figures, so the numeric guard (LLD-RT-05) is a non-issue.

## Problem 2 — explicit comparisons render an attribute table, grounded per item

An explicit "compare X and Y" / "X vs Y" / "difference between X and Y" now composes an attribute
table: **rows = attributes with units inseparable from the figure**, **columns = the compared
products**; a cell is filled only from that product's tool results (numeric guard unchanged — never
from memory); a row missing for one compared product is **omitted**, never an empty / "Not
specified" cell; each compared item's source is cited.

**Why a runtime extension was needed (and what the live trace showed).** The parse
(`PRODUCT_QUERY_SCHEMA`) carried a single `model_or_family`, so a compare fetched at most one
product and scoped retrieval to that one family. The live turn `f0d5b413` ("compare your process
compressors and natural gas compressors") grounded its table only because the parse returned
`model_or_family=null` → `list_products(industrial)` + a division-scoped `search_documents(k=3)`
that *happened* to return both a Process and a Natural-Gas chunk — **co-occurrence luck** — and it
still emitted a "Prime Mover | Not specified" cell. Grounding **by design** requires fetching each
named item and widening retrieval.

Minimal, authorised change (touches tool results only — no routing/gate/data change):
- `src/agentkit/runtime/schemas.py` — `PRODUCT_QUERY_SCHEMA` gains `compare_items: [string]` (the
  2–4 explicitly named products/families; empty otherwise).
- `src/agentkit/runtime/agents/product_advisor.py` — when `compare_items` ≥ 2 and a division is
  known: a structured lookup per item (`get_product` for product-level items, which scopes that
  item's search to its matched family) + `list_products(division)` (grounds **family-level** items
  such as the process/gas families, which carry capability rows but **no** `facts.product` rows, so
  `get_product` cannot see them) + a **targeted retrieval per item** (family-scoped when resolved,
  else division-scoped on the item name) so each item's specs surface. Each item's structured
  lookup **and** the division listing are force-cited, so every compared family is grounded (a
  citation per compared item, not one thin chunk). Fewer than two items, or no division → the
  branch falls through to the standard single/overview path, so the **co-occurrence fallback is
  preserved unchanged**.

### Persona table-rule reconciliation

`_persona.md` now states: a table earns its place for (1) an **explicit comparison** of 2+ named
products/families (holds even at two), or (2) three-or-more variants / a per-type spec set. The
prose-siblings-at-two presentation survives **only** for variant mentions riding along a
single-product answer — never for an explicit compare request.

## Goldens (LLD-EVAL-01)

Suite 55 → **58**. Assertion mechanisms (deterministic, robust to LLM variance; none weakens an
existing guard):
- `prod-overview-industrial` (en) — `must_not_contain: ["25000"]` (the Process family's published
  max Nm³/hr, which appears only in an enumeration/spec dump, never in a faithful grouped overview)
  + minimal `contains`; `list_products` fact block; grounds `fam.process_recip`.
- `prod-compare-process-natgas` (en, **cross-family** so it exercises the multi-fetch and fails if
  that path regresses) — `expected_answer_contains: ["|", "process", "natural"]` (the pipe proves a
  Markdown table rendered — persona prose never emits `|`; the two nouns prove both columns);
  `expected_source_ids: ["fam.process_recip", "fam.natural_gas_recip"]` (per-item family grounding).
- `prod-overview-industrial-hi` (hi) — the clustered overview in Devanagari: `must_not 25000` +
  a Devanagari copula anchor `है`, guarding the overview SHAPE holds in Hindi (answered, clustered,
  no spec dump). **`script_purity: hi` was tried and removed**: a broad overview enumerates many gas
  names, an inherently large register-leak surface (the model tends to keep gas/brand names in
  Latin), so strict register on this shape is not deterministic — a fragile assertion, not a robust
  one. Register purity stays guarded by the bounded existing e2e-hindi goldens (single-family
  duties). A Hindi *comparison* golden is deferred as the higher-variance case (table cells in Hindi).

The existing `script_purity` / romanised-Hindi direction-guard goldens are untouched except by
these additions.

### Hindi register: persona anti-leak rule (supports the three-exception requirement)

Live checks surfaced two register failure modes in Devanagari replies: (a) **parenthetical English
glosses** (`क्षमता (capacity)`, `ऑयल-फ्री (non-lubricated)`) and (b) **gas/attribute/brand names kept
in Latin** (`Oxygen`, `Natural Gas`, unbolded `Jyotech`). Two lines were added to the `_persona.md`
Devanagari register block, at the correct global home (they help every customer-facing agent, not
just product_advisor): transliterate gas names, attribute values and descriptive words (never leave
them Latin), and never append a parenthetical English gloss after a Hindi word. `product_advisor.md`
carries a scoped echo of the no-gloss rule for the table/overview shapes.

## Root cause of the Hindi-register failure — `LLM_DISABLE_THINKING`, not model drift

While bringing up the gate, `e2e-hindi-hydrogen` (a pre-existing golden with `script_purity: hi`,
routed to `application_discovery`) failed the e2e layer: Devanagari replies leaked Latin (English
glosses like `ऑयल-फ्री (non-lubricated)`, Latin gas/attribute words) and intermittently no-matched.

**Root cause: `LLM_DISABLE_THINKING=1`.** The Devanagari register needs compose **reasoning** at this
model size; with thinking off, `hi` compose degrades (Latin glosses, no-match). The knob was only
ever validated on the pre-Hindi 53-question suite; PR #10's 55/55 gate ran with thinking **on**.
Measured: with `hi` thinking on, the hydrogen duty is register-clean and answered (~150 s/turn);
`en`/`hinglish` compose thinking-off is fast (~7 s/turn). (An earlier A/B that swapped only the prompt
version was inconclusive — both arms shared the same thinking setting, which dominates the register
far more than prompt wording.)

### The per-language thinking knob — shipped UNCERTIFIED, default OFF

`compose_thinking_off` makes `LLM_DISABLE_THINKING=1` disable thinking for the compose call
(`schema_name=="answer"`) **only when the reply language is `en` / `hinglish`; `hi` always keeps
thinking on**. Knob **unset/0 (the default) = thinking on everywhere** (PR #10 behaviour, unchanged);
the per-language logic is inert unless someone sets the knob.

- `runtime/llm.py::compose_thinking_off(knob, schema_name, language)` — the pure gate
  (`knob and schema=="answer" and language != "hi"`); `RuntimeClient.raw_complete` gains a
  `language` param and calls it.
- `runtime/agents/base.py::compose_grounded_answer` stamps the turn's reply language onto the
  compose seam via a `compose_language` attribute (same idiom as `last_tokens`); the runtime seam
  reads it, other seams ignore it — the shared 3-arg `CompleteFn` signature is unchanged.
- Unit-tested across the 3 languages × knob on/off matrix (`tests/runtime/test_runtime_llm.py`).

**`LLM_DISABLE_THINKING=1` is NOT certified and must NOT be made the default.** Gating the full
58-question suite under it did **not** deterministically pass: with `hi` correctly thinking, a
**rotating `en` golden** failed each full run — `co-founded` (citation precision; measured ~4/5
clean) on one run, `cap-natgas-unit-conversion` (compose dropped the word "natural") on the next —
because `en`/`hinglish` **thinking-off** degrades compose citation/completeness. This is not
retry-farmable (the failure moves), so knob=1 stays an opt-in capability pending a follow-up that
hardens and certifies it.

**Follow-up targets before knob=1 can be certified/defaulted (M7):**
1. `faq_company` **over-citation under thinking-off** — a founding-year question cited 8–15 sources
   and sometimes dropped the required company-fact chunk.
2. `application_discovery` **completeness under thinking-off** — a natural-gas duty answered from the
   Process family without naming "natural gas".
3. The **`catalogue_testing` release-data pollution** surfacing in citations — a data-hygiene bug
   **independent of the knob**. Located (read-only, not deleted in this branch): the **active
   release `r2026.08.2`** carries a testing document **`doc.catalogue_testing`** ("Catalogue -
   Jyotech", `https://www.jyotech.com/catalogue-testing.html`) that was ingested and promoted. It
   contributes **8 `facts.company_fact` rows** (`cf.doc_catalogue_testing_s000.0–4`, `s001.0–2`) —
   crucially **two duplicate `founded=1990` facts** that collide with the legitimate
   `cf.doc_about_s000.0`, so `get_company_fact(kind=founded)` returns several and the compose can
   cite the test one instead (the observed `co-founded` failure). Follow-up: exclude
   `catalogue-testing.html` from the corpus (`sources.yaml` / chunking disposition) and re-promote a
   clean release; likely also has orphan chunks under `doc.catalogue_testing`. **No data deleted
   here** (facts are release-scoped and promote-only).

### Gate — certified thinking-on (PR #10's config)

The definitive gate runs with `LLM_DISABLE_THINKING` **unset** (thinking on for all languages —
exactly the config PR #10 certified 55/55 under): the full three-layer `prompt activate` gate over
the complete suite, then a standalone all-layer `eval run` as the definitive PASS. `hi` composes
clean because it thinks; `en`/`hinglish` compose precisely because they think. No golden weakened; no
retry-farming.

## Rule-6 flags (for the next doc pass)

- **LLD-AG-02 / LLD-TOOL**: `product_advisor` now parses an explicit `compare_items` list (cap 4)
  and, for an explicit comparison, does a structured lookup + targeted retrieval per item, citing
  each compared family (via `get_product` where product rows exist, else the division listing); the
  division-scoped co-occurrence path is preserved as the fallback. Note the data-model constraint
  that motivated the hybrid: process/gas families carry capability rows but no `facts.product`
  rows, so `get_product` cannot resolve them by name.
- **LLD-RT presentation rule (LLD.md line 124) / LLD-AG-02**: a table is taken for an explicit
  comparison of 2+ named products/families (even at two); the prose-siblings-at-two presentation is
  now scoped to single-product variant mentions only. A broad division/range ask is answered as a
  clustered orientation (3–5 clusters, no spec recitation), not an enumeration.
- **LLD-EVAL-01**: golden suite 55 → 58 (`prod-overview-industrial`,
  `prod-compare-process-natgas`, `prod-overview-industrial-hi`); the two new shape-assertion
  mechanisms (overview spec-figure `must_not`; comparison pipe + both product names, cross-family).
- **LLD-RT / LLD-RT-07 (runtime knob semantics)**: `LLM_DISABLE_THINKING` gains **per-language**
  behaviour when set — thinking-off applies to the compose call for `en`/`hinglish` only; `hi`
  always keeps thinking on (the Devanagari register needs compose reasoning at this model size).
  `compose_thinking_off` in `runtime/llm.py`; reply language threaded to the compose call via a
  `compose_language` seam attribute in `runtime/agents/base.py`. **Ships UNCERTIFIED, default off**
  (thinking on everywhere) — knob=1 did not deterministically pass the gate (rotating `en` failures),
  so it must not become the default until the M7 follow-up hardens and certifies it.

## Process note (README branch-activation rule)

The README rule the task asked to note — *"a feature-branch session that activates prompt versions
either re-activates the prior active set before it stops, or the branch merges promptly"* — was
found **already present** in `docs/README.md` §"Prompt versions and the live bot" (added by the
Hindi PR). No README edit was made; the rule is followed operationally: any activation from this
branch is either merged promptly or rolled back to the prior active set before stopping.
