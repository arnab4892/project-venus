---
title: Milestone 1 — schema, seed, and match_capability
date: 2026-08-23
author: Claude Code (paired with arnab.sharma)
type: implementation note (not a CR — no PRD change)
lld_items: [LLD-DB-01, LLD-DB-02, LLD-DB-06, LLD-TOOL-01, LLD-EXT-09]
prd_row: PRD-F-003
branch: milestone-1-schema-seed-match-capability
---

# Milestone 1 — schema, seed, and the first working tool

First real slice of the data plane and the first runtime tool, on top of the
Step 0 skeleton (`0000_bootstrap`). Build order per CLAUDE.md: DB migration +
seed → first tool. No LLM calls; nothing writes to `facts.*` except the seed;
no client-specific strings in `src/agentkit/`.

## 1. What was implemented

### Database — `migrations/versions/0001_init.py` (LLD-DB-01/02/06)
- All ten `facts.*` tables (`release`, `document`, `product_family`, `product`,
  `capability_row`, `capability_gas`, `company_fact`, `office`, `region_state`,
  `chunk`) exactly as `docs/design/data-model.md` §2 specifies.
- **Composite keying** (see decision Q1): every fact table carries `release_id`;
  PK is `(natural_id, release_id)` (`capability_gas` is `(cap_id, release_id,
  gas)`); all intra-`facts` FKs are composite and same-release.
- **`facts.active_<table>` view per table** (10 views): natural ids of the
  single active release; the tool layer reads only these.
- **Partial unique index** `uq_release_one_active` on `release(is_active) WHERE
  is_active` → exactly one active release.
- **`staging.*` mirrors** of the seven content tables (`document`,
  `product_family`, `product`, `capability_row`, `capability_gas`,
  `company_fact`, `office`), keyed by `release_candidate_id`, plus
  `evidence/confidence/review_status/reviewer/reviewed_at` (LLD-DB-06).
- No `vec.*` tables — those are per-release, built by the embedder (LLD-DB-03).
- Working `downgrade()` (drops views → staging → facts; leaves the extension and
  schemas to `0000`). Verified by a round-trip test.

### Seed — `src/agentkit/release/seed.py` + `clients/jyotech/seeds/demo.yaml`
- `agentkit seed demo`: idempotent (delete-by-release then insert), marks its
  release active (deactivating any other), and asserts FK closure at the end.
- Loads 57 fact rows under `r2026.08.1` (active): 1 release, 6 documents, 12
  product families, 8 products, 3 capability rows + 7 gases, 10 company facts,
  5 offices, 5 region_state.
- The loader is framework-generic (auto-detects the single `clients/*` dir); the
  data lives entirely under `clients/jyotech/`.

### Tool — `src/agentkit/tools/match_capability.py` + `src/agentkit/extract/normalise.py`
- `match_capability(conn, gas, capacity, capacity_unit, discharge_p,
  lubricated?, standard?)` → `{matches:[{cap_id, family_id, headroom:{capacity,
  pressure}}], near_edge}` (LLD-TOOL-01). Pure over the `active_*` views.
- `headroom = 1 − value/limit` per dimension; `near_edge` when any ratio > 0.9.
- Unit handling in `extract/normalise.py` (LLD-EXT-09): canonicalises
  `Nm³/hr | Nm3/hr | NM3/HR → Nm3/hr`, `bar | barg → barg`, and converts flow
  with the documented constant `1 SCMD = 1/24 Nm3/hr` (`1 Nm3/hr = 24 SCMD`).

### CLI — `src/agentkit/cli.py`
- `agentkit seed demo [--client]`
- `agentkit tools match-capability --gas --capacity --unit[=Nm3/hr]
  --discharge-p --oil-free/--lubricated [--standard]` (prints JSON).

### Supporting
- `src/agentkit/db/engine.py`: shared engine/connection helper.
- Tests (written test-first):
  - `tests/tools/test_match_capability.py` — six cases from `data-model.md` §4:
    hydrogen in-range (→ `cap.002`/`fam.process_recip`, headroom 0.85/0.65,
    near_edge false); pressure out-of-range (1200 barg → no match); wrong-gas
    (oxygen @ 350 barg vs cap.001's 50-barg ceiling → no match); near-edge
    (19,500 Nm³/hr → near_edge true); SCMD conversion (1000 Nm³/hr lubricated →
    `cap.003` only); and a two-release **active-view flip** proving the views
    (and tool) follow `release.is_active`, plus a one-active-release guard.
  - `tests/db/test_migration_roundtrip.py` — `upgrade → downgrade → upgrade` on a
    throwaway `jyotech_migtest` database.

### Verification (clean slate)
`docker compose down -v && up` → `db upgrade` → `seed demo` (clean, idempotent)
→ `pytest -q` (10 passed) → the hydrogen query returns `cap.002` with headroom
`{0.85, 0.65}` / `near_edge false`, and the 1200-barg query returns no matches —
matching the §4 trace exactly.

## 2. Deviations & notes
- **Composite PKs deviate from the literal single-column PKs shown in
  `data-model.md` §2.** This is required to let releases coexist for
  promote/rollback (LLD-DB-02, LLD-REL-05) and was confirmed in Q1. The sample
  tables in the doc show natural ids because that is the shape seen *through* the
  `active_*` views; a "Keying rule" note was added to §2 to make this explicit.
- **`data-model.md` was edited in this commit** (not via `/prd-change`): a
  doc-accuracy fix aligning the `.md` to `data-model.html` and the seed —
  added the `fam.search_eq` family row and the `doc.contact` / `doc.index`
  document rows, plus the keying-rule note. HLD.md and LLD.md were **not**
  touched. Authorised explicitly by the user (Q2).
- **`source_doc_id` kept NOT NULL + enforced FK** (no rows dropped, not made
  nullable) — provenance is a hard constraint (PRD-F-015). Required adding
  `doc.index` and `doc.contact` so every `company_fact`/`office` source resolves
  (Q2).
- **Staging carries no cross-table FKs.** It holds unreviewed/rejected rows and
  FK closure is a release-import gate (LLD-REL-03), so enforcing FKs in staging
  would be wrong. This is an interpretation of LLD-DB-06 ("mirrors") — flagged
  here for the review/release milestone.
- **`region_state` modelled as a full `facts` table** (`state, release_id,
  region, office_id` with composite FK to `office`) with its own `active_` view,
  matching `data-model.html` (three columns), not the two-column sketch in the
  `.md` (Q3).
- **`str(URL)` masks passwords.** The migration round-trip test initially failed
  auth because `str(sqlalchemy.URL)` renders the password as `***`; fixed by
  `render_as_string(hide_password=False)`. Noted in case other test/CLI code
  round-trips a URL through a string.
- **Pre-existing Alembic `path_separator` deprecation warning** (from
  `alembic.ini`) left untouched to avoid scope creep.
- **Not committed to `main` directly:** work was done on
  `milestone-1-schema-seed-match-capability` and fast-forwarded onto `main`.

## 3. Ambiguities raised and how they were resolved

Three inconsistencies/ambiguities between the docs and the sample seed data were
surfaced before coding (per CLAUDE.md: stop and ask rather than improvise).

### Q1 — Fact-table keying
**Question.** `data-model.md` §2 writes single-column PKs (`family_id`,
`cap_id`, …), but LLD-DB-02 requires every `facts.*` table to carry `release_id`
+ an `active_*` view, and LLD-REL-05 rollback needs old and new releases to
coexist in the same tables. How should facts tables be keyed?

**Answer (chosen: composite PK + release_id).** Every `facts.*` table gets
`release_id`; PK = `(natural_id, release_id)`; `capability_gas` PK =
`(cap_id, release_id, gas)`. All intra-facts FKs are composite and same-release,
e.g. `FOREIGN KEY (family_id, release_id) REFERENCES facts.product_family
(family_id, release_id)`. `facts.active_*` views filter to the active release
and expose the natural id; the tool layer reads only the views — no code outside
the release module filters `release_id` directly. Exactly one active release is
enforced by a partial unique index on `release(is_active) WHERE is_active`. Seed
keeps the sample ids with `release_id = 'r2026.08.1'` and that release active.
`staging.*` mirrors carry `release_candidate_id` with the same keying. Update
`data-model.md` §2 with a "Keying rule" note stating that all facts tables are
keyed `(natural_id, release_id)` with composite same-release FKs, and that the
sample tables show natural ids as seen through the `active_*` views. Add a test:
with two releases seeded (copy `r2026.08.1` as an inactive `r-test`),
`match_capability` returns exactly one `cap.002`, and flipping `is_active` flips
which release's values the views serve.

### Q2 — Seed FK closure (two broken sample rows)
**Question.** Two sample rows break FK closure: `prd.proeye951 → fam.search_eq`
(family undefined in the `.md`), and several `company_fact`/`office` rows cite
sources (`index.php`, `contact.php`, `about.html`) not present as document PKs.
How should `seed demo` handle these so it runs clean?

**Answer (none of the offered options exactly).**
(a) Keep `prd.proeye951` and **add** the missing family `fam.search_eq`
(division `fire_rescue`, category "Search & Rescue", name "Search Cameras & Life
Detectors", source `doc.fs §Search & Rescue`) — it exists in
`data-model.html` but was omitted from the `.md` sample.
(b) Seed a **closed document set**: add `doc.index`
(`https://www.jyotech.com/index.php`) and `doc.contact`
(`https://www.jyotech.com/contact.php`), and map every filename source
(`index.php`, `contact.php`, `about.html`) to its `doc_id` so all FKs resolve.
Do **not** make `source_doc_id` nullable or drop FK enforcement — provenance is a
hard constraint (PRD-F-015) — and do not skip fact rows. Fix the `.md` sample
tables in the same commit (add the `fam.search_eq` row and the two document
rows) so the doc and seed agree. Seed must end FK-closed, with an assertion at
the end of `seed demo` that fails if any fact row's FKs don't resolve.

### Q3 — `staging.*` and `region_state` scope
**Question.** What `staging.*` and `region_state` scope should `0001_init`
create now?

**Answer (chosen: content mirrors; region_state as fact).** `staging` mirrors
only the seven content tables (`document`, `product_family`, `product`,
`capability_row`, `capability_gas`, `company_fact`, `office`), keyed by
`release_candidate_id` + the review columns. `region_state` is a `facts` table
(with `release_id` + an `active_` view). No staging mirror for `release` or
`region_state`.

## 4. Traceability
`PRD-F-003` row updated — Code: `tools/match_capability.py,
extract/normalise.py`; Tests: `tests/tools/test_match_capability.py`.
