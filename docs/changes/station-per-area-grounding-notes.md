# Station task — per-area grounding + name resolution — working notes

Branch: `station-per-area-grounding` (off `main` @ `9fcc7fa`, PR #15 merged @ `842a093`).
LLD ids in scope: name resolution PRD-F-004 (LLD-AG-02 / TOOL-02 / TOOL-03), grounding PRD-F-008.
Design: LLM decides *what* the question engages (areas, names); code resolves + fetches; the
grounding gate is untouched. No keyword lists. Exhibit-C rule: a name match that leaves meaningful
tokens of the customer's phrase unaccounted for is not a match.

## Step 0 — preconditions

- Preconditions PASS: `git log` shows `842a093` (PR #15 merge) and `9fcc7fa` (LLD 1.19) on `main`.
  PR #14 = `65ecf40` (get_product family-envelope). Branch `station-per-area-grounding` created.

### 0.2 — Active prompt-version snapshot (BEFORE any activation)

`SELECT prompt_id, version FROM ops.prompt_version WHERE is_active ORDER BY prompt_id;` →

| prompt_id | version |
|---|---|
| pv.jyotech.after_sales_intake.23 | 23 |
| pv.jyotech.application_discovery.27 | 27 |
| pv.jyotech.commercial_routing.23 | 23 |
| pv.jyotech.deflect.1 | 1 |
| pv.jyotech.documents_compliance.23 | 23 |
| pv.jyotech.faq_company.27 | 27 |
| pv.jyotech.product_advisor.23 | 23 |
| pv.jyotech.triage.27 | 27 |

**Rollback recipe** (revert-and-reactivate): to restore the pre-task runtime,
`git checkout main -- clients/jyotech/prompts/`, then re-activate the versions above with
`agentkit prompt activate <prompt_id>` for each changed prompt (only `application_discovery` and
`product_advisor` are touched by this task, so in practice:
`agentkit prompt activate pv.jyotech.application_discovery.27` and
`agentkit prompt activate pv.jyotech.product_advisor.23`). Activation is golden-gated; a clean
`main` checkout re-activates cleanly. New inactive versions loaded during the task are harmless
(inactive) and can be left in `ops.prompt_version`.

Environment: DB `postgresql+psycopg://agent:***@localhost:5433/jyotech_v1`; runtime LLM
`cyankiwi/Qwen3.8-27B-AWQ-INT4` @ `103.48.50.161:8000` (auth on; reachable — 401 without key);
embedder @ `103.48.50.161:11972`. e2e / live turns run via the `chat`-style Ctx wiring
(`build_runtime_client().complete_fn()`, `embed=None` → search_documents builds its own embedder).

### 0.3 — Exhibit B reproduction (GAP STANDS)

Question: "What natural gas compressors do you guys offer?" (single turn, active release
`r2026.08.2`, rolled-back savepoint). Verbatim trail:

```
route=product_advisor  outcome=answered
  triage
  product_advisor
    TOOL list_products({'division':'industrial','category':None}) -> rows=20
    TOOL search_documents({'query':'natural gas compressors','division':'industrial','k':3}) -> rows=3
  citations=[fam.air_separation_plant, fam.biogas_*, fam.ccdu_3in1, ... fam.natural_gas_recip,
             ... (every industrial family from the list_products dump) ...,
             ch.process_gas_compressors.0000, doc.process_gas_compressors, ...]
BOT: "Our dedicated natural gas line is **Natural Gas Compressors (Motor & Gas Engine Driven)**.
      Natural gas also falls under our **Process Compressors (Recip.)** range ... If you share the
      gas composition, flow, and suction/discharge pressure, I can narrow it to the right machine."
```

**Confirmed:** `get_product` NEVER fired — the parse emitted no `model_or_family` (the PR #14
fall-through can only resolve a name it is handed; none was). The answer names both lines but
fetched ZERO family rows for `natural_gas_recip`; the published 10,000–100,000 SCMD / up to
120 barg envelope is never stated. `fam.natural_gas_recip` is cited only because it rides in the
20-family `list_products` dump, not a targeted fetch. Both halves of the fix are needed:
extraction must write down the engaged area, and the resolver must match it.

### 0.5 — Registry survey (active release `r2026.08.2`)

59 families (industrial / fire_rescue / diving); 111 product rows (101 with a printed
`model_name`). MCH variants are **separate families**: `fam.mch_engine_bac` "MCH-16 Engine
(Petrol / Diesel)", `fam.mch_smart_bac` "MCH-13/16 Electric (Smart Series)" (product variant
"Smart Series"), `fam.mch_ergo_bac` "MCH-13/16 & 21/23 Electric (Ergo Series)",
`fam.mch_silent_bac`, `fam.mch_high_capacity_bac`, `fam.mch6_portable_bac`.

**CURRENT matcher probe (the wrongs this task fixes):**

| query | current `matched_by` | current family | correct target |
|---|---|---|---|
| `natural gas compressors` | **None** | — | `fam.natural_gas_recip` (Exhibit B) |
| `Smart MCH-16` | `model_contains` | **`fam.mch_engine_bac`** ✗ | `fam.mch_smart_bac` (Exhibit C) |
| `Ergo MCH-16` | `model_contains` | **`fam.mch_engine_bac`** ✗ | `fam.mch_ergo_bac` (Exhibit C) |
| `smart mch 16` / `ergo mch 16` | `model_contains` | **`fam.mch_engine_bac`** ✗ | smart / ergo |
| `CNG boosters` / `CNG booster` | **None** | — | ambiguous: hydraulic vs portable |
| `air separation` (singular) | **None** | — | `fam.air_separation_plant` (now resolves) |
| `hydrogen refuelling station` | **None** | — | (application phrase — LLM emits the clean area) |

**CURRENT matcher — already correct, must stay GREEN (token-coverage is a fallback after these
exact/sibling tiers, which are untouched):**

| query | `matched_by` | family |
|---|---|---|
| `MCH-16` | `model` (sibling) | engine+ergo+smart+silent (4 fams) |
| `BATTERY POWERED COMBI-TOOL` | `model` (exact) | `fam.battery_rescue_tools` |
| `C-Monitor` | `model` (exact) | `fam.diving_accessories` |
| `Process Compressors (Recip.)` | `family_envelope` (alias-exact) | `fam.process_recip` |
| `hydrogen fuelling systems` | `family_envelope` (exact-ish) | `fam.hydrogen_fuelling_system` |
| `hydrogen compressors` | `family_envelope` | `fam.hydrogen_compressor` |
| `air separation plants` (plural) | `family_envelope` | `fam.air_separation_plant` |
| `diaphragm compressors` / `oxygen compressors` | `family_envelope` | resp. families |

**Token-coverage predictions (candidate token bag = family_name + its products' model_name +
variant, split on whitespace/`-`/`/`/`()`/`.`/`&`; numbers kept):**
`natural gas compressors` → `fam.natural_gas_recip` (covers {natural,gas,compressors}=3, unique).
`Smart MCH-16` → `fam.mch_smart_bac` (covers {smart,mch,16}=3; engine leaves "smart" dangling).
`Ergo MCH-16` → `fam.mch_ergo_bac`. `CNG boosters` → **ambiguous, exactly 2 candidates**
(`fam.hydraulic_cng_booster`, `fam.portable_cng_booster` both cover {cng,boosters}) → the
disambiguation golden + live-check #5 target. `air separation` → `fam.air_separation_plant`
(covers {air,separation}=2, unique; "separation" is the distinguishing token).

**Chosen disambiguation phrasing (rider 2):** **"CNG boosters"** — customer-typed primary name,
clean 2-way tie (Hydraulic CNG Boosters / Portable CNG Boosters), within the 2–4 clarify cap.

**Amendment-2 before/after to report at gate time:** `cap-air-separation` — the parse may now hand
`get_product` a resolvable name; inspect its answer/citations before/after and confirm the golden
stays GREEN with correct citations.

## Step 4 — live checks (verbatim trails; new prompt application_discovery.28 active, thinking-on)

All turns via the `chat`-style Ctx (`build_runtime_client().complete_fn()`, embed=None), release
`r2026.08.2`, rolled-back savepoint (no ops rows kept).

1. **Refuelling follow-up (EN), PASS.** Turn 1 states 3,000 Nm³/hr hydrogen duty (application_
   discovery → Process Compressors envelope). Turn 2 "Actually this is for a hydrogen refuelling
   station…" → product_advisor, `additional_areas=["hydrogen fuelling systems"]`, trail:
   `get_product("hydrogen fuelling systems")` (family_envelope) + `search_documents(family_ids=
   ["fam.hydrogen_fuelling_system"])`. Citations include **`cap.doc_hydrogen_compressors_and_
   fueling_system_s000.1`** + `cap.doc_jyotech_catalog_process_s024.0` + `fam.hydrogen_fuelling_
   system`. Answer carries "SAE J2601 2020", "ISO 19880", "Quick Fill (Flow Control System)",
   "Class 1, Division 1,2, Group B". (Also verified as a single-turn "What do you offer for a
   hydrogen refuelling station?" — same trail + citations — the station-golden anchor.)
2. **Same in Hindi, PASS.** `get_product` fired for the fuelling family, same cap citation, J2601/
   ISO 19880/Quick Fill present, Devanagari register intact (product names/units/standards stay
   Latin per the register rule).
3. **"What natural gas compressors do you guys offer?" — PASS (Exhibit B fixed).** Before this
   task: `list_products`+`search` only, no figures. After: `additional_areas=["natural gas
   compressors"]` → `get_product` (family_envelope) → citations `fam.natural_gas_recip`,
   `cap.doc_jyotech_catalog_process_s007.0`, `cap.doc_process_gas_compressors_s000.2`; answer states
   **"10,000–100,000 SCMD, up to 120 barg, API-11P / ISO 13631"** (the published envelope, never on
   the table before). Fix landed within the single `additional_areas` contract line (broadened to
   capture the central line when `model_or_family` is null).
4. **"compare smart mch 16 and ergo mch 16" — PASS (Exhibit C fixed).** `get_product("Smart MCH 16")
   → fam.mch_smart_bac`, `get_product("Ergo MCH 16") → fam.mch_ergo_bac` (DIFFERENT families in the
   trail), per-item searches scoped to each family; two-column table renders; no "same family"
   claim. Was: both → fam.mch_engine_bac.
5. **Disambiguation clarify — NOT reached E2E (open item, see below).**
6. **Plain single-family duty (3,000 Nm³/hr hydrogen, 20→350 bar, oil-free) — PASS (no regression).**
   application_discovery, `additional_areas` empty, no extra fetch, answer unchanged in substance
   (Process Compressors (Recip.), up to 25,000 Nm³/hr / 1,000 barg, oil-free, API-618).

### Open item — the disambiguation clarify path is E2E-unreachable for the surveyed phrase

The clarify (ambiguous PRIMARY name → one question listing 2–4 candidate display names) is
implemented and **unit-tested** (`_token_coverage` ambiguous verdict; product_advisor returns
`action="clarify"`). But it does not fire E2E for "CNG boosters": the extraction classifies a
product-LINE name as an `additional_area` (or, for "oxygen compressors", an exact-plural confident
match), never the primary `model_or_family`. Per confirmed **Option 1**, an ambiguous `additional_
area` **skips** (never asks). Model-NUMBER ambiguity ("MCH-13") is absorbed by the sibling tier
(returns all variants), also never a clarify. So no natural customer phrasing routes an ambiguous
string into the primary slot. Raised with the user before finalizing goldens/gate.

**User decision (this session):** unit-test the clarify as a deterministic safety net (done —
`tests/agents/test_product_advisor.py::test_product_advisor_ambiguous_primary_asks_clarify`), drop
the E2E live-check #5 and any E2E disambiguation golden as unreachable-by-design, and do NOT reroute
line-names to `model_or_family` to force a test to fire. The skip-trace on ambiguous areas
(`output.skipped_areas`) is the ops tripwire: if a genuinely ambiguous PRIMARY name ever appears in
the wild, revisit reachability then. Positive framing: today's ambiguous natural phrasings are
resolved by ANSWERING (list-overview / sibling-naming), not asking.

## Step 3 — gate (definitive, thinking-on / certified config, LLM_DISABLE_THINKING unset)

`agentkit eval run jyotech` (all three layers, application_discovery.28 active, release
`r2026.08.2`), verbatim:

```
fact       46/46, na=16
retrieval  6/6, na=56
e2e        62/62
runtime: 1497.3s
RESULT: PASS
```

Every golden GREEN, including the two new (`cap-station-hydrogen-fuelling`,
`prod-compare-smart-ergo-mch16`) and the whole containment-replacement regression set
(`prod-combi-tool`, `prod-cmonitor`, `prod-diablo`, `prod-mch16-specs`, `prod-nameless-cabinet`,
`cap-false-memory-product`, `cap-air-separation`).

**Final certified activation:** `agentkit prompt activate pv.jyotech.application_discovery.28` →
"Golden suite passed (fact+retrieval+e2e)." Active prompts now: `application_discovery.28`,
`product_advisor.23` (unchanged — its extraction change is code-side in `_PARSE_SYSTEM`, not the DB
compose prompt). Rollback recipe (Step 0.2) unchanged: revert prompts + re-activate .27/.23.

### Amendment-2 enumerate-and-inspect (before/after)

- **`cap-false-memory-product` — UNCHANGED.** Trail identical to PR #14: `get_product("Process
  Compressors (Recip.)")` hits the alias-EXACT envelope tier (token-coverage is only the fallback,
  so exact still wins); answer "published maximum … is 25,000 Nm³/hr, not 30,000"; cites
  `fam.process_recip` + `cap.doc_jyotech_catalog_process_s005.0`. No drift.
- **`cap-air-separation` — GREEN, strictly better grounding (the legitimate Amendment-2 change).**
  Before: family cited via the list/search fallback ("get_product doesn't resolve this family by
  name"). After: `additional_areas=["air separation plants for nitrogen"]` → `get_product`
  (family_envelope) → now cites `fam.air_separation_plant` + capability rows
  `cap.doc_air_seperation_plants_s000.0` / `cap.doc_jyotech_catalog_process_s034.1` by a deliberate
  fetch. Golden's `fam.air_separation_plant` still present → passes; new citations correct; answer
  ("Air Separation Plants … High-Purity Nitrogen Plants, cryogenic, VPSA …") carries no forbidden
  figure.

## Files changed

- `src/agentkit/tools/get_product.py` — `_tokens` (word-preserving, plural-folded tokeniser),
  `_token_coverage` (Pareto-frontier scorer), `_family_result` + `_envelope`; unified the
  exact-then-token-coverage family resolution, replacing the two longest-substring containment
  sites. New return contract: `matched_by:"ambiguous"` + `candidates`.
- `src/agentkit/runtime/schemas.py` — `additional_areas` on `APPLICATION_SCHEMA` and
  `PRODUCT_QUERY_SCHEMA` (properties + required); mirrors `compare_items`. No DB migration.
- `src/agentkit/runtime/agents/base.py` — `ground_additional_areas` (shared per-area multi-fetch,
  cap 3, fail-open, family-scoped search, force-cite, skip-trace).
- `src/agentkit/runtime/agents/product_advisor.py` — one `_PARSE_SYSTEM` `additional_areas` line;
  ambiguous-primary → `action:"clarify"`; per-area fetch + force-cite + `skipped_areas` output.
- `src/agentkit/runtime/agents/application_discovery.py` — `additional_areas` in `_extract_slots`;
  per-area fetch on the answer path + force-cite + `skipped_areas`.
- `clients/jyotech/prompts/application_discovery.md` — one facts-block line (→ prompt v28, activated).
- `clients/jyotech/golden/questions.yaml` — +2 goldens (60→62).
- tests — `tests/tools/test_tools.py` (token-coverage), `tests/runtime/test_additional_areas.py`
  (per-area fetch), `tests/agents/test_product_advisor.py` (clarify). Full suite 376 passed.
- `docs/TRACEABILITY.md` — PRD-F-002 / F-004 / N-005 Code+Tests columns.

## Rule-6 doc flags (for the /prd-change → /propagate-prd doc pass; no LLD/HLD/PRD edited here)

1. **LLD-TOOL-03** — get_product's containment fallback is now TOKEN-COVERAGE (Pareto-frontier over
   family name+product tokens, plural-folded), not longest-substring; adds an `ambiguous` verdict
   (2–4 co-maximal families → `candidates`), unified across the product and family-envelope sites.
2. **LLD-AG-02** — product_advisor: `additional_areas` parse field drives a per-area multi-fetch
   (cap 3, fail-open, family-scoped retrieval, force-cite); an ambiguous PRIMARY name yields a new
   `action:"clarify"` (one question listing candidate display names).
3. **LLD-AG-01** — application_discovery: `additional_areas` slot drives the same per-area fetch on
   the answer path (kept out of the required/duty slots so it never gates match_capability).
4. **Runtime JSON contract** — `additional_areas: [string]` on APPLICATION_SCHEMA and
   PRODUCT_QUERY_SCHEMA (no DB migration; rides the existing ops JSON payloads).
5. **Ops visibility** — ambiguous/unresolved additional areas are traced in `invocation.output.
   skipped_areas` (and the logger); the ops-review tripwire for clarify reachability.
6. **LLD-EVAL-01** — golden suite 60 → 62 (`cap-station-hydrogen-fuelling`,
   `prod-compare-smart-ergo-mch16`).
7. **Deferred/unreachable** — the ambiguous-primary clarify is unreachable by natural phrasing today
   (line-names route to additional_areas; model-number ambiguity → sibling tier); kept as a
   unit-tested safety net (per user decision), revisit if ops shows a genuine case.
