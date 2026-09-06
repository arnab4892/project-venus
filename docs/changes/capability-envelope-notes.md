# Capability-family envelope lookup — change notes (branch `capability-envelope-lookup`)

Rule 6: no edits to `docs/LLD.md` / `HLD.md` / `PRD.md`. Doc-worthy design changes are flagged here
and mirrored as "rule 6 flag" annotations in `docs/TRACEABILITY.md` (PRD-F-004, PRD-F-008, PRD-N-005).

## Problem (live miss, turn 66df4ebb-2861-43d9-a90f-cf906bb01541)

On the product route, a false-memory challenge about Process Compressors ("someone told me the max
was 30,000 Nm³/hr") could not restate the published figure. The parse extracted the family name and
`product_advisor` called `get_product("Process Compressors (Recip.)")`, but that family — like every
industrial family except `fam.ccdu_3in1` — carries **capability rows but no `facts.product` rows**, so
`get_product` (SQL anchored on `facts.active_product`) returned 0 rows. The 25,000 Nm³/hr envelope was
then reachable only by retrieval luck; when retrieval missed, the turn fell back.

## Change (additive)

1. **`src/agentkit/tools/get_product.py` — family-envelope fall-through.** Only on the existing 0-row
   fall-through: resolve the name against the full family registry (`facts.active_product_family`)
   with the *same* rules already in the file — alias-exact on `family_id`/`family_name`, then ≥4-char
   longest-wins containment — and return the family's published envelope taken **verbatim** from
   `facts.active_capability_row` (+ `facts.active_capability_gas`): `{matched_by: "family_envelope",
   products: [], family: {...}, capabilities: [per-row {capacity/discharge/unit, standards,
   lubricated, comp_type, gases, source ids}]}`. Product-backed lookups return earlier and are
   **byte-identical** to before.
   - **Result shape = per-capability-row**, not an aggregated envelope: lossless, unit-safe (a family
     can hold rows in different units — a cross-row max would be meaningless), reuses the
     `match_capability` renderer, and feeds the multi-construction table. Gases fold into each row.
   - **Empty-envelope case:** a family that resolves but has zero capability rows returns
     `matched_by: "family_envelope"`, `family` present, `capabilities: []` — never `None`/crash.

2. **`src/agentkit/runtime/agents/product_advisor.py`** — standard path: when `get_product` returns a
   `family_envelope`, use it as the structured source (`structured_rec`) and scope retrieval to the
   family, so the compose can state the published limits instead of only listing the family name via
   `list_products`. The existing force-cite carries its `tr_id`. Also guarded a latent bug: the
   retrieval-query seed indexed `products[0]`, which is empty on the envelope path. **The compare path
   is unchanged** — it reads `products` only and ignores the envelope.

3. **`src/agentkit/runtime/agents/base.py`, `render_tool_context`** — renders the per-row envelope
   (capacity/discharge via `format_number`, figure+unit inseparable; standards, type, gases). An
   empty envelope renders the family name + summary, no figures.

4. **`src/agentkit/runtime/grounding.py`, `_citations_from_record`** — derives `family` + per-row
   `capability` + `document` citations from a `family_envelope` result. This is honest-parent
   enrichment of the **cited** result (the same pattern the `match_capability` branch uses); the
   numeric/citation **gate semantics are unchanged**. Chosen over the lighter `families[]`-key
   fallback so the Sources panel carries the capability row + its source document, not just the family.

## Decisions

- **`must_not_contain` on the new golden uses full-figure adoption forms** (`up to 30,000` / `up to
  30000` / `maximum is|of 30,000` / `… 30000`), not the task's literal bare `["30000","30,000"]`:
  a correct refutation legitimately *names* 30,000 to reject it, the numeric guard independently
  blocks any unsourced 30,000, and the short forms ("maximum of 30") would substring-collide with a
  legitimate "maximum of 300 bar". (User-confirmed override of the task's literal wording.)
- **Scope held to the single/overview path.** The parse returns a family name for challenges that lead
  with it (the documented live-miss shape); bare category challenges parse to null and keep their
  existing `list_products` + retrieval behaviour. No parse-prompt change, no prompt re-version — the
  existing challenge rule (`clients/jyotech/prompts/product_advisor.md`) already says to restate the
  published figure, now with deterministic data behind it.

## Data note (tests vs eval)

Unit tests run on the **demo seed** (`seeded_conn`, release `r2026.08.1`): `fam.process_recip` is named
"Process Gas Compressor", `cap.002` capacity_max **20000** Nm³/hr. The golden suite runs on the **live
active release**: name "Process Compressors (Recip.)", capacity_max **25000** Nm³/hr. Assertions are
keyed to the right dataset in each place.

## Gate & regression

- `pytest -q`: **359 passed** (352 baseline + 7 new: envelope resolve by name/id, empty-envelope,
  product-match-carries-no-envelope, unknown-no-envelope, citation family/capability/document
  derivation, render figure+unit inseparable, render empty-envelope).
- Standalone all-layer `agentkit eval run jyotech` over the **59**-question suite under **thinking-on,
  temp=0**: **RESULT PASS — e2e 59/59, fact 45/45, retrieval 6/6** (runtime ~19 min). No prompt
  changes were needed, so no prompt-activate gate / activation; the standalone run is the definitive
  PASS (task-mandated before merge).
- **Live acceptance:** `"I heard your Process Compressors (Recip.) max at 30000 Nm3/hr — is that the
  published figure?"` — thinking-on 4/4 and thinking-off (`LLM_DISABLE_THINKING=1`) 4/4: `answered`,
  states 25,000 Nm³/hr, never adopts 30,000, cites the process family. The envelope path is proven to
  fire end-to-end by a `capability` citation (`cap.doc_jyotech_catalog_process_s005.0`) that only the
  §4 grounding branch can emit. One thinking-on answer:
  > No — the published maximum I have for **Process Compressors (Recip.)** is up to **25,000 Nm³/hr**,
  > not 30,000 Nm³/hr. This line is reciprocating, non-lubricated or lubricated, water cooled, and
  > designed to API-618 or equivalent, with discharge up to **1000 barg**. …

### Enumerate-and-inspect (not green-only)

Every existing golden whose question could route to `get_product` for a capability-only family was
enumerated and its captured answer + citations inspected in the standalone run for drift. Result:
**zero drift — the envelope path is inert for all of them**, because their `get_product` calls either
don't happen or use a partial name that doesn't resolve:

| Golden | get_product call | Path taken | Verdict |
|---|---|---|---|
| `cap-hydrogen-fuelling` | none (parse → null) | list_products + retrieval (pre-existing) | unchanged |
| `cap-air-separation` | none (parse → null) | list_products + retrieval | unchanged |
| `cap-false-memory` | none (parse → null) | list_products + retrieval | unchanged (correct 25,000 refutation) |
| `prod-overview-industrial` | none (parse → null) | list_products clustered overview | unchanged |
| `prod-compare-process-natgas` | "Process Gas"/"Natural Gas" → **0 rows** (partials don't resolve) | compare (listing + per-item retrieval) | unchanged; envelope never fired |

The envelope path fires only on an **exact full-family-name** `get_product` call (the documented
live-miss shape and the new `cap-false-memory-product` golden). The **compare path** reads `products`
only and, on partial names, `get_product` returns 0 rows anyway — so no compare golden is affected.
(A resolved family that returned an envelope in the compare path would still be ignored by that path's
`if products:` gate; only the single/overview path consumes the envelope.)
