---
title: Milestone 3b — review import, integrity, promote, activate, rollback
date: 2026-08-29
author: Claude Code (paired with arnab.sharma)
type: implementation note (+ two clarification-path items flagged for the next doc pass; no PRD/HLD change)
lld_items: [LLD-REL-02, LLD-REL-03, LLD-REL-04, LLD-REL-05, LLD-DB-02, LLD-DB-06, LLD-DB-07]
prd_row: PRD-F-014 (promote/rollback), PRD-F-015 (review gate now end-to-end)
branch: milestone-3b-release
---

# Milestone 3b — review import, integrity, promote, activate, rollback

Fifth milestone in the CLAUDE.md build order. Milestone 3a produced the review
workbook and stopped; 3b closes the loop specified by LLD 1.7: pull the reviewer's
decisions back into `staging.*`, check integrity, diff against the active release,
and **promote the approved+edited set into `facts.*`** under a new release that can
be activated and rolled back. This is the **first code that writes `facts.*` from
staged content** (only the demo seed did before). Embedding (LLD-RET) and the full
golden suite (LLD-EVAL) are **out of scope** — promote prints that embedding is
deferred; a small smoke check stands in for eval.

The human review of `rc.jyotech.0001` was complete and treated as **read-only
input** (never re-exported). Three carry-over fixes from the 3a verification landed
first, then the four new commands.

## 1. What was implemented

### Carry-over fixes
- **`release/export.py` `_cell`** — an empty attributes list (`{"printed": []}`) now
  renders a **blank cell**, not the leaked repr `printed=[]`. Empty-valued dict
  entries are skipped.
- **`clients/jyotech/schemas.py` gas evidence** — `build_rows` no longer re-derives
  a gas row's evidence from the (model-lowercased) value (`evidence={"gas": gas}`);
  it keeps the **gate-approved quote** in its source case. The verbatim gate
  (`extract/evidence.py`) was already whitespace-only/case-sensitive; a test now
  pins the case rejection. The stored gas **value stays verbatim** (`H2`, not
  translated).
- **`release/export.py` gas sheet provenance** — `capability_gas` has no source
  columns of its own (its provenance is the parent `capability_row`'s, by design —
  LLD-DB-02/§2.4). The gas sheet now fills `source_doc_id`/`source_locator`
  **display-only** from the parent so the reviewer sees the origin. No staging/facts
  schema change; applies to **future exports only** (rc.jyotech.0001 was not
  regenerated — a test covers the behaviour).

### Import — `release import <rc> <xlsx>` (`release/import_review.py`, LLD-REL-02)
Matches rows by sheet + natural id + rc. Validates **before any write** (decisions
∈ approve/edit/reject; any edited `family_id` in `families.yaml`; every decided row
exists in staging). Applies `approve→approved`, `reject→rejected`, blank→stays
`pending`, `edit→edited` **and writes back the value columns** (family_id, units,
numerics, arrays, needs_family; jsonb `attributes`/`detail` are lossy in the display
and left as staged). A rejected `capability_row` **cascades** to its
`capability_gas` children. `capability_gas` has no editable value columns, so an
edit there is decision-only. RC → `imported`; per-sheet decision counts reported.

### Integrity — `release check <rc>` (`release/check.py`, LLD-REL-03)
On the approved+edited set only. **Hard failures:** reference closure
(product/capability_row `family_id` in `families.yaml`; each referenced family's
parsed source doc is a surviving document; source_doc_id of each source-carrying row
is a surviving document); `capability_gas` has a surviving parent; provenance
present on the source-carrying tables (product/capability_row/company_fact/office —
capability_gas inherits, document is the source, region_state exempt per LLD-DB-07);
**a capacity/discharge numeric with no unit** (listed with its evidence quote);
duplicate natural ids; `needs_family` still true on a survivor; **a generic
`facts` NOT-NULL-column present-on-survivors** safety net so promote never dies on a
raw DB error; an office whose city does not resolve via `region_state`. **Warnings:**
a family with no surviving row; surviving rows sharing a `conflict_group`. The
office→region mapping is printed for human eyeballing.

### Curated region_state seed (`clients/jyotech/seeds/region_state.yaml`, LLD-DB-07)
Curated from the 12 reviewed offices: `state → region`, keyed to a serving office,
with an auxiliary `cities` list the check uses to resolve `office.city → state`.
Uttar Pradesh/Delhi→North, Maharashtra→West, Tamil Nadu→South, West Bengal→East,
Singapore→Intl. Only `(state, region, office_id)` is inserted into
`facts.region_state`; `office_id` is a surviving office so the composite FK closes.

### Diff — `release diff <rc>` (`release/diff.py`, LLD-REL-04)
Compares surviving capability rows against `facts.active_capability_row` by `cap_id`,
listing changed numerics. Does not assume zero overlap; the first real promote (only
the demo release active, no id overlap) reports **"everything is new"** cleanly.

### Promote / activate / rollback (`release/promote.py`, LLD-REL-05)
**Gate:** refuse an RC with no ledger row or status ∉ {exported, imported} (so
`rc.jyotech.bootstrap` and open RCs are never promotable). **Promote** mints a new
`rYYYY.MM.N` release (inactive), inserts the approved+edited rows into `facts.*`
FK-safe (reusing `seed.py`'s `_insert_row`/`_assert_fk_closed`); `facts.product_family`
is built from the frozen `families.yaml` (only families referenced by a surviving
row, `source` parsed into doc/locator — multiple `;`-separated tokens tolerated);
`facts.region_state` from the curated seed. RC → `promoted`; per-table counts and an
**embedding-deferred** line printed. **Activate** flips the single active release
(partial unique index enforces one); **rollback** is the same flip aimed at a prior
release.

### CLI (`cli.py`)
`release import | check | diff | promote | activate | rollback` added (Typer,
mirroring `release export`).

### Tests (test-first; 22 new; `pytest -q` → **126 passed**, ruff clean)
`tests/release/`: `test_import` (decision semantics, edited write-back, gas cascade,
validation), `test_check` (each hard failure + warnings + office resolution),
`test_diff` (new/changed + everything-new), `test_promote` (gate refusals, FK
closure, product_family from yaml, active-view flips on activate/rollback).
`tests/release/test_export`: blank-attributes + gas-sheet provenance.
`tests/extract/`: `test_evidence_required` case cases, `test_frozen_family` gas
evidence keeps source case. `tests/tools/test_match_capability`: non-comparable unit.

## 2. Deviations & notes
- **capability_gas provenance is inherited, not stored (clarification-path, fold into
  LLD-DB-06 / data-model §2.4).** `capability_gas` is `(cap_id, gas)` only in both
  staging and facts — no source columns, by design. The provenance gate applies to
  the source-carrying tables; capability_gas is gated **relationally** (a surviving
  gas must have a surviving parent — provenance is transitive through the composite
  FK). The 3a "fix #1" (populate gas source columns / import backfill) therefore
  reduced to nothing on the data side; only the export gas sheet gained a display-only
  parent join.
- **`facts.product.model_name` relaxed to nullable — migration 0003 (clarification-
  path, fold into data-model §2.3).** 10 approved products are catalogue-listed by
  category/variant with **no printed model number** (Filling Panels, Fill Containment
  Cabinets, CBRN masks, Under Water Communication). `model_name` is "as printed" and
  is legitimately null when the catalogue prints no model; inventing one would break
  the never-guess rule. Downgrade re-adds NOT NULL. **For the tool/runtime milestone:**
  such a product must be presented by its **family name + variant/description**, never
  a blank or an invented name.
- **Release id format is `rYYYY.MM.N` (data-model §2.1), not the brief's
  `rel.jyotech.NNNN`.** The brief's form was an error; this promote minted
  `r2026.08.2` (next N in the month after the seed `r2026.08.1`). RC ids stay
  `rc.<client>.NNNN` — the two namespaces are intentionally different.
- **Deferred to the tool/runtime milestone (LLD-TOOL), facts stay verbatim:**
  (a) a per-client **gas alias map** (`hydrogen→H2`, `oxygen→O2`, `helium→He`, …)
  applied at query time — the smoke uses the stored `H2`, not `hydrogen`;
  (b) `match_capability` **null-boolean filter semantics** — a null `lubricated`
  means "unspecified / both offered" and must not be excluded by a lubricated filter;
  (c) extend the **normalise unit vocabulary** (cfm, lpm, lumen, tons, TPD, kg/hr,
  psi, W, kg/cm2g). Any null-unit **or** unknown-unit numeric is treated as
  **non-comparable** (never guessed). As a minimal robustness fix this milestone,
  `match_capability` now skips the comparison for a row whose unit is outside the
  canonical vocabulary (returned with `None` headroom on that dimension) instead of
  crashing — it hit a real `Kg/hr` hydrogen row during the smoke.
- **`LLD.md` was not edited** — 1.7 already specifies this milestone; the two
  clarification-path items above are for the next doc pass.

## 3. Live run status (real `rc.jyotech.0001`, dev Postgres)
**Import.** `release import rc.jyotech.0001` → RC `imported`. Decision counts (read
from the reviewed workbook): document 26 approve · product 101 approve / 10 edit ·
capability_row **27 approve / 36 edit / 4 reject** · capability_gas 76 approve /
5 reject (+ cascade) · company_fact 134 approve · office 12 approve. (No blanks and
no rejected company_fact in this RC; the edit pass had filled all units.)

**Check.** `release check rc.jyotech.0001` → **PASS**. Zero unit warnings (units
were filled on the updated workbook). 0 orphan-family warnings — **all 59 families
are referenced** by a surviving product/capability_row. 4 warnings, all surviving
`conflict_group`s the reviewer kept (hydrogen_compressor ×5, online_cng_compressor
×3, ppv_blower ×3, smoke_exhauster ×2). All 12 offices resolved via `region_state`;
the printed mapping: NOIDA→Uttar Pradesh→North, New Delhi→Delhi→North,
Mumbai→Maharashtra→West, Chennai→Tamil Nadu→South, Kolkata→West Bengal→East,
Singapore→Singapore→Intl.

**Diff.** `release diff rc.jyotech.0001` → "everything is new — 63 capability rows,
0 changed numerics (no overlap with the active release); 3 active rows superseded."

**Promote.** `release promote rc.jyotech.0001` → **r2026.08.2** (inactive). facts.*
row counts: release 1 · document 26 · product_family **59** · product 111 ·
capability_row **63** · capability_gas 76 · company_fact 134 · office 12 ·
region_state 6. 10 name-less products promoted (thanks to migration 0003).
Embedding-deferred line printed; RC → `promoted`.

**Activate + smoke.** `release activate r2026.08.2` → active. Via the `active_*`
views + `tools/match_capability`: the 25000 Nm3/hr process row
(`cap.doc_jyotech_catalog_process_s005.0`) is present and the rejected 20000 row is
absent; `match_capability(gas='H2', capacity=3000, Nm3/hr, discharge_p=350)` returns
`fam.process_recip` with **headroom {capacity 0.88, pressure 0.65}** (matching
data-model §4's ~0.65 pressure); active office count = 12; the 4 rejected capability
rows and their gases are absent from the active views. (No company_fact was rejected
in this RC, so that sub-assertion is vacuously satisfied; rejection is proven via the
absent 20000 row + gases.)

**Rollback drill.** activate `r2026.08.2` → active caps = the new release's process
row; `rollback r2026.08.1` → `facts.active_release` = `r2026.08.1`, active caps =
the demo `cap.002`; re-activate `r2026.08.2` → back to the new release. The
`active_*` views switched each time. Final state: **r2026.08.2 active**.

## 4. Exit criteria status
- Carry-over: export blank attributes ✔ · gas evidence keeps source case ✔ · gas
  sheet provenance display-only ✔
- Import applies approve/edit(write-back)/reject/blank; rejected-parent gas cascade;
  RC→imported; decision counts reported ✔
- Integrity: reference/provenance/duplicate/needs_family/unit/NN hard checks +
  conflict/orphan warnings + office→region_state mapping ✔ (real RC PASS)
- Diff vs active release, "everything is new" handled ✔
- Promote gate (no ledger row / bootstrap / status open refused, tested) ✔; promote
  inserts facts.* with FK closure; product_family from families.yaml; region_state
  from curated seed; embedding deferred (printed, not failed) ✔
- Activate/rollback flip the single active release; rollback drill proven ✔
- Smoke (stand-in for LLD-EVAL) passes on the real promoted release ✔
- `pytest -q` green (**126**) ✔ · ruff clean ✔
- Real `rc.jyotech.0001` imported → checked → diffed → promoted (**r2026.08.2**) →
  activated → smoke → rollback drill, all against dev Postgres ✔

## 5. Traceability
`PRD-F-014` (LLD-REL-02…05, LLD-DB-02) — Code: `release/` (import_review, check,
diff, promote, curated), `clients/jyotech/seeds/region_state.yaml`,
`migrations/versions/0003_product_model_name_nullable.py`, cli
`release import|check|diff|promote|activate|rollback`; Tests: `tests/release/`
(test_import, test_check, test_diff, test_promote). `PRD-F-015` — review gate now
end-to-end; carry-over fixes recorded (export `_cell`, schemas gas evidence,
match_capability non-comparable units). `LLD.md` unchanged (two clarification-path
items flagged above for the next doc pass).
