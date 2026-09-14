# Offer discipline + FAQ citation discipline — execution & verification notes (branch `offer-citation-discipline`)

Pre-demo prompt batch. Two live-observed defects, both prompt-wording, both fixed with **verbatim
prompt edits only** — no code, no golden changes (suite stays 62). Offers are generative → live-check
territory, not goldens.

1. **Over-offering** — the persona requires a one-next-step close but nothing constrains it to
   *deliverable* things. Live: "When did you guys start?" → correct cited answer, then offered "a
   short overview of how we moved into compressor manufacturing"; customer said "Yes"; corpus has no
   such narrative → the bot declined its own offer. An accepted offer becomes the next turn's
   question, so an undeliverable offer is a guaranteed miss.
2. **FAQ over-citation** — single-fact company answers citing 8–15 sources incl. irrelevant docs.
   `faq_company.md` said "list the tool-result ids you used" with nothing defining "used".

`_persona.md` is prepended to the 6 persona agents at load time (single-source include,
`src/agentkit/runtime/prompts.py:49-58`), so the persona edit re-versions **all six** persona
agents. `faq_company` also carries its own body edit — still one version bump. `triage`/`deflect`
do not get the persona and are unchanged.

## 0. Rollback snapshot (recorded FIRST, before any activation)

Verbatim result of the required query, captured against the LIVE `ops.prompt_version` **before**
this branch touched any prompt:

```sql
SELECT prompt_id, version FROM ops.prompt_version WHERE is_active ORDER BY prompt_id;
```

| prompt_id | version |
|---|---|
| pv.jyotech.after_sales_intake.23 | 23 |
| pv.jyotech.application_discovery.28 | 28 |
| pv.jyotech.commercial_routing.23 | 23 |
| pv.jyotech.deflect.1 | 1 |
| pv.jyotech.documents_compliance.23 | 23 |
| pv.jyotech.faq_company.27 | 27 |
| pv.jyotech.product_advisor.23 | 23 |
| pv.jyotech.triage.27 | 27 |

(8 active rows. The persona edit bumps the **6 persona agents** — `after_sales_intake`,
`application_discovery`, `commercial_routing`, `documents_compliance`, `faq_company`,
`product_advisor` — so **6 new prompt versions** will be loaded and activated. `triage` and
`deflect` are unchanged and keep versions 27 and 1.)

### Rollback recipe

If this branch's activation must be undone:

1. `git revert -m 1 <merge-commit-of-this-branch>` (revert the branch's merge into `main`), which
   restores the two prompt files to their pre-branch state.
2. Re-load the reverted files: `agentkit prompt load jyotech` (creates fresh inactive versions of
   the reverted bodies).
3. Re-activate the snapshotted set above through the **full three-layer gate against the current
   suite** — `agentkit prompt activate <id>` for the 6 bumped rows (final one full e2e), so the live
   active set returns to exactly the versions listed. Rows are content-addressed by body: re-loading
   the reverted bodies reproduces the same version numbers only if bodies match — otherwise activate
   the highest-version row whose body equals the pre-branch file. Invariant: the reverted suite must
   pass all three layers before rollback is declared complete.

---

## 1. Edits (verbatim, two only)

### Edit 1 — `clients/jyotech/prompts/_persona.md`
In "Shaping the answer", a **new top-level bullet immediately after the "Spine of every answer"
bullet** (decision confirmed with user: new bullet, not mid-bullet). Verbatim:

> - **The next step must be something we can deliver.** Offer only (a) an action that always
>   works — connecting the visitor with our engineers or commercial team, asking for their duty
>   details, or pointing to a document already retrieved this turn — or (b) more detail on
>   material already present in this turn's tool results. Never offer content you have not seen:
>   no overviews, histories, documents, or figures that are not on the table right now. An
>   accepted offer becomes the next question — only write cheques the published data can cash.

### Edit 2 — `clients/jyotech/prompts/faq_company.md`
Line 18 replaced (the brief's quote was shorter than the actual line; user confirmed the merged
wording keeping the mechanism clause inline). Lines 19–20 ("Don't name a document…") intact.

- OLD: `List the tool-result ids you used in the citations array — the UI renders them as Sources.`
- NEW:

> Cite ONLY the results whose content your answer actually states — one fact, one source — in the
> `citations` array (the UI renders them as Sources). Citing everything returned is as wrong as
> citing nothing.

---

## 2. Gate results

**pytest:** `376 passed` (no test changes; suite stays 62 goldens).

**`agentkit prompt load jyotech`** — full-manifest reload created 8 fresh inactive versions, but only
the **6 persona agents** changed body, so only those 6 were activated (triage/deflect left on their
current active versions 27 / 1, bodies unchanged):
```
pv.jyotech.application_discovery.29
pv.jyotech.product_advisor.25
pv.jyotech.documents_compliance.25
pv.jyotech.after_sales_intake.25
pv.jyotech.commercial_routing.25
pv.jyotech.faq_company.29
```

**Batch bring-up (fact,retrieval), thinking-on** — 5 agents:
```
Activated pv.jyotech.application_discovery.29. Golden suite passed (fact+retrieval).
Activated pv.jyotech.product_advisor.25. Golden suite passed (fact+retrieval).
Activated pv.jyotech.documents_compliance.25. Golden suite passed (fact+retrieval).
Activated pv.jyotech.after_sales_intake.25. Golden suite passed (fact+retrieval).
Activated pv.jyotech.commercial_routing.25. Golden suite passed (fact+retrieval).
```

**Final activation — full three-layer thinking-on** (`LLM_DISABLE_THINKING=false agentkit prompt
activate pv.jyotech.faq_company.29`):
```
Activated pv.jyotech.faq_company.29. Golden suite passed (fact+retrieval+e2e).
```

Active set after activation (6 persona bumped; triage 27 / deflect 1 unchanged):
```
pv.jyotech.after_sales_intake.25    | 25
pv.jyotech.application_discovery.29 | 29
pv.jyotech.commercial_routing.25    | 25
pv.jyotech.deflect.1                | 1
pv.jyotech.documents_compliance.25  | 25
pv.jyotech.faq_company.29           | 29
pv.jyotech.product_advisor.25       | 25
pv.jyotech.triage.27                | 27
```

**Standalone definitive all-layer run — `LLM_DISABLE_THINKING=false agentkit eval run jyotech`**
(thinking-on, verbatim summary):
```
fact       46/46, na=16
retrieval  6/6, na=56
e2e        62/62

runtime: 1394.0s
RESULT: PASS
```
Suite = 62 goldens; no golden changes. Definitive PASS.

---

## 3. Live checks (verbatim, `agentkit chat jyotech --show-trace`, thinking-on)

### Check 1 — "When did you guys start?" → "Yes"  ✅ PASS (the target fix)
Turn 1 (routed faq_company, grounded status=full):
> Jyotech started in 1991, originally as an engineering consultancy firm dealing in trading of
> capital engineering equipment. Would you like a short overview of what the group does today?

The offer is now **deliverable** ("what the group does today" — present in the corpus), replacing
the old undeliverable "how we moved into compressor manufacturing." Turn 2 ("Yes") **delivered** it,
grounded (status=full):
> Jyotech serves the natural gas, hydrocarbon and petrochemical, steel and power, and fertilizer
> industries, along with defense. Clients include ONGC, Reliance Industries, TATA Steel, NTPC,
> Linde, BASF, the Indian Navy and CISF. We also cover air separation plants.
>
> Which industry or application are you working in? I can point you to the right catalogue.

No self-decline. The accepted offer became a delivered, grounded next turn — exactly the fix.

### Check 2 — hydrogen duty 3,000 Nm³/hr, 20→350 bar, oil-free  ✅ PASS (warmth intact)
Routed application_discovery, 5 claims grounded, closes with an allowed action:
> …That duty sits comfortably inside our **Process Compressors (Recip.)** range… Shall I have our
> engineers confirm the exact frame for your site?

### Check 3 — "Which certifications does Jyotech hold?"  ⚠️ CITATION PRECISION NOT ACHIEVED
Answer (states 6 certs):
> Jyotech holds ISO 9001:2015, ISO 14001:2015, ISO 45001:2018, EN, NFPA, and AMCA certifications.
> Would you like me to connect you with the team for certificate copies?

`get_company_fact {kind: certification}` returned **13 rows**; the final turn cited **15 rows —
12 value-matched facts + 3 parent documents** (verified against `ops.citation` for turn
`c7a2442e`). The answer consolidates to 6 distinct certs, so this is still the over-citation the
edit targeted. **But the attribution below proves the verbatim prompt edit could never move this
path** — the count is code-derived, not model-chosen. See §4.

### Check 4 — founding question in Hindi (Devanagari)  ✅ PASS (register + deliverable close + precise)
Routed faq_company (language=hi), grounded status=full, **2 citations** (precise):
> **Jyotech** की शुरुआत 1991 में हुई थी, जब यह एक इंजीनियरिंग कंसल्टेंसी फर्म के रूप में स्थापित हुई थी। क्या आप हमारी
> स्थापना या वर्तमान उपस्थिति के बारे में और जानना चाहेंगे?

Devanagari throughout, **Jyotech** bold-Latin, ASCII digit 1991, deliverable close (founding /
current presence both in corpus). Register intact.

**Live-check verdict:** the primary defect (over-offering) is fixed (checks 1, 2, 4). The
pulled-forward faq citation-precision item is applied verbatim and gate-green but under-delivers on
the high-fan-out `get_company_fact` path (check 3) — carried to §4 as a follow-up.

Note (out of scope, pre-existing): the bot asserts founding year "1991" (checks 1 & 4); per the
known founding-year data conflict (1990 web / 1991 catalogue) this is a separate corpus item, not
touched by this batch.

---

## 4. Rule-6 flags — two doc-pass targets for the next `/propagate-prd` cycle

These prompt edits move behavior ahead of the design docs. Next doc pass must fold them in:

1. **LLD AG preamble** — the "Shaping the answer" presentation paragraph gains the
   **offer-deliverability constraint**: the one-next-step close must be either an always-available
   action (connect with engineers/commercial team, ask for duty details, point to a document
   retrieved this turn) OR more detail on material already in this turn's tool results — never
   unseen content (no overviews, histories, documents, or figures not on the table).
2. **LLD-AG-06 (`faq_company`)** — **citation discipline**: cite only the results whose content the
   answer actually states; one fact, one source; citing everything returned is as wrong as citing
   nothing.
   - **Open follow-up (from live check 3) — citation attribution (5-min spec for the fix):**
     For the certifications turn (`c7a2442e`), the LLM's own `citations` array does **not** drive
     the final set — so no prompt wording will ever move the number on this path. The 15 final
     `ops.citation` rows are **all code-added**, in two code stages:
     1. **Force-cite** — `faq_company.run` (`src/agentkit/runtime/agents/faq_company.py:153-156`)
        appends the `get_company_fact` structured record's id whether or not the compose LLM cited
        it ("so its id is grounded even if the compose LLM omits it").
     2. **Derivation fan-out** — `_citations_from_record` (`src/agentkit/runtime/grounding.py:249-254`)
        expands that one record into one `fact` row per returned fact whose value tokens appear in
        the answer (`_value_used`) **plus that fact's parent `document`**. Here 12 of the 13 cert
        rows value-matched (the *same* certs recur as separate rows across 3 source documents —
        about page + both catalogues), giving 12 facts + 3 docs = 15.
     **Model-chosen: ~0 decisive. Code-added: 15/15.** Confirmed: the fix is **derivation/tool-side,
     not prompt** — dedupe `certification`-kind facts by normalized value before/at citation
     derivation (collapse a cert appearing in N documents to one fact + one representative source),
     e.g. in the `get_company_fact` company-fact branch of `_citations_from_record` or in the
     `get_company_fact` tool itself. Out of this batch's two-edit rights → separate scoped change.
   - **Success bar for the follow-up:** not the brief's "1–3" but **citations ≈ the facts the
     answer actually states** — a 6-cert answer citing ~6 distinct fact rows (+ their few parent
     documents) is *correct*; the defect is only the per-document duplication, not the count itself.
     (The `faq_company.md` verbatim edit still stands: it sharpens the model's own `citations` array,
     which does drive the document/search path — it simply cannot govern the force-cited
     company-fact fan-out.)
