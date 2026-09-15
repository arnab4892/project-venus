---
title: Compare path must not require a division — stale division guard on the multi-fetch
lld: [LLD-AG-02]
branch: fix/compare-division-guard
base: 630a69a (main — LLD 1.21, PR #17 merged)
---

# Compare path must not require a division (turn 80bda617)

## Problem

"Compare Smart MCH-16 and Ergo MCH-16" with triage `division=unknown` produced NO
`get_product` calls — only a bare `search_documents` — and the bot declined the side-by-side
(turn 80bda617). Cause: the PR #11 compare multi-fetch was guarded by

```python
if len(compare_items) >= 2 and division is not None:   # ← stale precondition
```

That `division is not None` precondition dated from when family-level compare items could
only be grounded via the division listing. Post-PR #16 the shared token-coverage resolver +
family envelope ground any item without a listing, so the precondition is stale. The
behaviour flipped on triage's per-run division guess: a run that guessed `division=industrial`
took the per-item path and produced the correct two-column table; the `division=unknown` run
collapsed to a bare search and a decline.

## Fix (scoped — `product_advisor.py` compare branch only)

- The compare branch now gates on the two named items ALONE: `if len(compare_items) >= 2:`.
  The per-item loop (`get_product` per item via the shared resolver → per-item family-scoped
  retrieval → force-cite) runs independent of triage division.
- The division listing is fetched only when triage carries a real division
  (`listing_rec = tools.list_products(division) if division is not None else None`) — additive
  grounding for family-level items, exactly as before — and force-cited only when present.
- No other behaviour change: ambiguity, absence, and single-item paths untouched. Product-level
  items already resolved division-independently via `get_product` + a family-scoped search; the
  fix simply stops the whole branch from being skipped when division is unknown.

## Tests

- New unit: `tests/agents/test_product_advisor.py::test_compare_per_item_fetch_is_division_independent`
  — a compare with triage `division="unknown"` (→ normalised to None) still performs per-item
  `get_product` for BOTH items, does NOT fetch the division listing, and still grounds both
  families (force-cited from the per-item lookups, not the listing).
- Existing compare tests unchanged and green:
  `test_compare_product_level_fetches_each_and_cites_each_family` (product-level, fire_rescue),
  `test_compare_family_level_grounds_via_listing` (family-level via the division listing — the
  regression-#3 shape), `test_single_product_path_unchanged_when_no_compare_items`.
- `pytest -q`: **377 passed**.

## Flaky-count history for `prod-compare-smart-ergo-mch16` (retry-once absorption?)

Checked the two most recent certified gate reports:

- PR #16 (`station-per-area-grounding-notes.md`): `e2e 62/62` — **no `N flaky` suffix**.
- PR #17 (`offer-citation-discipline-notes.md`): `e2e 62/62` — **no `N flaky` suffix**.

Per the housekeeping item-2 policy (`housekeeping-notes.md` §Item 2), the per-layer summary
prints `e2e 62/62, N flaky` whenever an e2e question fails once and passes on retry. Both
certified runs show a plain `62/62`, so **retry-once was NOT absorbing the division roll** —
the golden simply drew a division that took the working path on both certified runs. That is
exactly why this latent flake escaped the gate and only surfaced on a live turn (80bda617):
the golden is one draw of triage's division guess, and it happened not to draw `unknown`. This
fix removes the dependency entirely, so the golden no longer rides on that draw.

## Gate — standalone all-layer certified run

`LLM_DISABLE_THINKING=false agentkit eval run jyotech` (thinking-on, certified config), verbatim
summary. `prod-compare-smart-ergo-mch16` GREEN (e2e). Definitive PASS:

```
fact       46/46, na=16
retrieval  6/6, na=56
e2e        62/62

runtime: 1598.9s
RESULT: PASS
```

No `N flaky` suffix on any layer — a clean 62/62.

## Live checks (verbatim answers + tool trails, `agentkit chat jyotech --show-trace`, thinking-on)

### Check 1 — fresh session, "Compare Smart MCH-16 and Ergo MCH-16"  ✅ PASS (the target fix)

Triage reported **`division:unknown`** — the exact roll that used to collapse the turn — and the
per-item path STILL fired. Trail:

```
triage {division:unknown intent:product_question language:en in_scope:True confidence:0.88}
  tool_call get_product args={"model_or_family": "Smart MCH-16"} rows=2
  tool_call get_product args={"model_or_family": "Ergo MCH-16"} rows=2
  tool_call search_documents args={"query": "Smart MCH-16 specifications", "family_ids": ["fam.mch_smart_bac"], "k": 3} rows=2
  tool_call search_documents args={"query": "Ergo MCH-16 specifications", "family_ids": ["fam.mch_ergo_bac"], "k": 3} rows=2
grounding {"claims": 3, "grounded": 3, "status": "full"}
```

Per-item `get_product` for BOTH items with DIFFERENT families (`fam.mch_smart_bac` /
`fam.mch_ergo_bac`), family-scoped retrieval per item, and — because `division` is unknown — NO
`list_products` call. Both families cited. Verbatim answer:

> Here is the side-by-side comparison:
>
> | Attribute | **MCH-13/16 Electric (Smart Series)** | **MCH-13/16 Electric (Ergo Series)** |
> |---|---|---|
> | F.A.D. | 200 - 300 lpm | 200 - 300 lpm |
> | Op. pressure | 200/225 or 300/330 bar | 200/225 or 300/330 bar |
> | Prime mover | 4 / 5.5 kW electric motor (3 phase) | 4 / 5.5 kW electric motor (3 phase) |
> | Control | Separate control switchboard with start/stop/emergency stop, hour meter, selectable pressure switch for auto stop, auto drain | Built-in control panel with start/stop/emergency stop, hour meter, auto stop, auto drain, low oil, high temperature and reverse rotation protection |
> | Design | Portable, compact, mountable on fire tenders | Open-frame |
>
> The key difference is the Smart's compact, fire-tender-mountable design with a separate
> switchboard versus the Ergo's open-frame built-in protection panel - which installation are you
> planning?

Before this fix, a `division:unknown` run produced NO `get_product` calls (bare search) and a
declined side-by-side (turn 80bda617). Fixed.

### Check 2 — same question after an MCH-16 turn (division context present)  ✅ PASS (unchanged)

Turn 1 "Tell me about the MCH-16" established fire-rescue context; turn 2's triage then reported
**`division:fire_rescue`**. The compare trail:

```
triage {division:fire_rescue intent:product_question language:en in_scope:True confidence:0.95}
  tool_call get_product args={"model_or_family": "Smart MCH-16"} rows=2
  tool_call get_product args={"model_or_family": "Ergo MCH-16"} rows=2
  tool_call list_products args={"division": "fire_rescue", "category": null} rows=33
  tool_call search_documents args={"query": "Smart MCH-16 specifications", "family_ids": ["fam.mch_smart_bac"], "k": 3} rows=2
  tool_call search_documents args={"query": "Ergo MCH-16 specifications", "family_ids": ["fam.mch_ergo_bac"], "k": 3} rows=2
grounding {"claims": 3, "grounded": 3, "status": "full"}
```

Same per-item two-column table as Check 1 — AND the division listing is additively fetched now
that `division` is real (`list_products("fire_rescue")`, force-cited over the fire-rescue
families). Exactly the intended "listing additive when division known" behaviour; the per-item
grounding is identical to the unknown-division run.

### Check 3 — regression: "Can you compare your process compressors and natural gas compressors?"  ✅ PASS (unchanged)

Family-level comparison, `division:industrial`. Trail:

```
triage {division:industrial intent:product_question language:en in_scope:True confidence:0.95}
  tool_call get_product args={"model_or_family": "Process Compressors"} rows=0
  tool_call get_product args={"model_or_family": "Natural Gas Compressors"} rows=0
  tool_call list_products args={"division": "industrial", "category": null} rows=20
  tool_call search_documents args={"query": "Process Compressors specifications", "division": "industrial", "k": 3} rows=3
  tool_call search_documents args={"query": "Natural Gas Compressors specifications", "division": "industrial", "k": 3} rows=3
grounding {"claims": 4, "grounded": 4, "status": "full"}
```

The two families carry no product rows (`get_product` rows=0 for both), so the division listing
(`list_products("industrial")`, rows=20) still grounds the family-level comparison exactly as
today, and the per-item searches fall to division scope (no matched family). Both families cited
(`fam.process_recip`, `fam.natural_gas_recip`). Verbatim answer:

> Here's how the two lines sit side by side:
>
> | Attribute | **Process Compressors (Recip.)** | **Natural Gas Compressors (Motor & Gas Engine Driven)** |
> |---|---|---|
> | Type | Reciprocating | Reciprocating |
> | Lubrication | Non-Lubricated/Lubricated | Lubricated |
> | Cooling | Water cooled | Air cooled |
> | Configuration | Vertical / V-Type / W-Type / Horizontal | Horizontal Balanced Opposed |
> | Capacity | up to 25,000 Nm³/hr | 10,000 - 100,000 SCMD |
> | Suction pressure | As per requirement | As per requirement |
> | Discharge pressure | up to 1,000 barg | up to 120 barg |
> | Standard | API-618 or equivalent | API-11P / ISO 13631 |
> | Applications | H2, hydrocarbon gases, natural gas, BOG, mixed gas | Natural gas; upstream wellhead gas compressor, gas lift up compressor, midstream pipeline booster |
>
> The key difference is that the process line covers wider gases and much higher discharge
> pressure, while the natural gas line is built for wellhead and pipeline booster duty — which gas
> and discharge pressure are you sizing for?

## Active prompt versions (unchanged — no prompt changes in this fix)

```
after_sales_intake.25   application_discovery.29   commercial_routing.25
deflect.1               documents_compliance.25    faq_company.29
product_advisor.25      triage.27
```

No prompt changes → no activation cycle.

## Rule-6 doc flag (for a later /prd-change → /propagate-prd doc pass; no LLD/HLD/PRD edited here)

- **LLD-AG-02** — the compare multi-fetch description drops its division precondition. Per-item
  grounding is division-independent (shared token-coverage resolver + family envelope, PR #16);
  the division listing is additive grounding fetched only when triage carries a real division.
  The current LLD-AG-02 wording ("Fewer than two items or no division → the standard
  single/overview path") should become "Fewer than two items → the standard single/overview
  path".
