# Presentation intelligence — execution & verification notes (branch `presentation-intelligence`)

The prompt drafts (persona shape menu + verdict-first spine, shape-tagged exemplars across six
agents, triage need-vs-ask redefinition) were authored externally on this branch (commits
`94eb2e9`, `db62057`). This session is **mechanics and verification**, not prompt rewriting
(bounded surgical edits only, each documented below). Rule 6 flags for the next doc pass are
collected at the end.

## 0. Rollback snapshot (recorded FIRST, before any activation)

Verbatim result of the required query, captured against the LIVE `ops.prompt_version` before this
branch activates anything:

```sql
SELECT prompt_id, version FROM ops.prompt_version WHERE is_active ORDER BY prompt_id;
```

| prompt_id | version |
|---|---|
| pv.jyotech.after_sales_intake.22 | 22 |
| pv.jyotech.application_discovery.26 | 26 |
| pv.jyotech.commercial_routing.22 | 22 |
| pv.jyotech.deflect.1 | 1 |
| pv.jyotech.documents_compliance.22 | 22 |
| pv.jyotech.faq_company.26 | 26 |
| pv.jyotech.product_advisor.22 | 22 |
| pv.jyotech.triage.17 | 17 |

(8 active rows. `_persona.md` is prepended to the 6 persona agents at load time, so this branch's
persona edit bumps all six; each of those six agent files also changed, and `triage.md` changed —
so **7 new prompt versions** will be loaded and activated. `deflect` is unchanged and keeps v1.)

### Rollback recipe

If this branch's activation must be undone:

1. `git revert -m 1 <merge-commit-of-this-branch>` (revert the branch's merge into `main`), which
   restores the prompt files and golden suite to the pre-branch state.
2. Re-load the reverted files: `agentkit prompt load jyotech` (creates fresh inactive versions of
   the reverted bodies).
3. Re-activate the snapshotted set above through the **full three-layer gate against the reverted
   suite** — i.e. `agentkit prompt activate <id>` for each of the 8 rows (final one full e2e), so
   the live active set returns to exactly the versions listed, gate-proven against the reverted
   goldens. (The snapshot rows are content-addressed by body; re-loading the reverted bodies
   reproduces the same version numbers only if the bodies match — otherwise activate the
   highest-version row whose body equals the pre-branch file. The safe invariant: the reverted
   suite must pass all three layers before the rollback is declared complete.)

---

## 1. Code — after-sales completion close → per-language templates

The one build item. `src/agentkit/runtime/agents/after_sales_intake.py`: the English f-string
completion close is now `_CLOSE_TEMPLATES = {en, hi, hinglish}`, selected on `triage.language`
(fallback `en`), interpolating `model / city / need / office / contact_detail` + the optional
office-email parenthetical `{contact}` — the same `_TEMPLATES` idiom as `commercial_routing` and
`application_discovery`'s `_NOMATCH_TEMPLATES`. The en template preserves the prior wording
verbatim (so the existing en completion test is unchanged). The hi template's fixed scaffolding is
register-pure written Hindi; the visitor's / tool's own values (model, city, need, contact detail,
office email) stay verbatim via `{placeholders}` — they are runtime-filled technical values, not
prose to translate (identical treatment to the nomatch/commercial templates).

Unit tests (`tests/agents/test_after_sales_intake.py`, +4):
- `test_completion_close_english` / `_hinglish` / `_hindi` — a full `run_turn` with the reply
  language set on triage reaches the completion close and produces the right script + names the
  office and carries the visitor's own values (Kolkata / MCH-16 / a service visit / the email)
  verbatim.
- `test_hi_close_template_is_register_pure` — `script_purity_offenders(_CLOSE_TEMPLATES["hi"]) == []`
  using the **shared** checker the eval gate uses (mirrors `test_hi_commercial_handoff_is_register_pure`);
  the checker strips `{placeholders}`, so only the fixed Hindi scaffolding is asserted pure.

All 8 after_sales unit tests pass (4 prior + 4 new).

## 2. Goldens (suite 59 → 60)

Approved mechanism (Option 1 + the two riders). Both anchors reuse the validated pipe-negative
`must_not_contain: ["|"]` — a Markdown pipe proves a table rendered; persona prose never emits one,
the same anchor `prod-compare-process-natgas` trusts in the positive. Zero new retrieval luck (the
facts are independently golden-proven by `co-founded` / `faq-founder`). Existing shape guards
untouched.

- **NEW `faq-founded-and-founder`** (two-part → ordered prose). `expected_answer_contains:
  ["199", "Deepak Bhatia"]` + `must_not_contain: ["|"]`; `fact` block on the **founder** fact
  (`get_company_fact founder → cf.doc_about_s000.1`); `expected_source_ids: [cf.doc_about_s000.1]`.
  **Rider (a):** the golden guards the two-part SHAPE, not the conflicted founding figure — the
  year is asserted only with the loose `199` prefix, **exactly as the green `co-founded` golden
  does**, which does not strictly pick 1990 (website) vs 1991 (catalogue), the known unresolved
  data conflict. Both substrings present ⇒ both parts were answered; the pipe-negative ⇒ ordered
  prose, not a table.
- **STRENGTHENED `co-founded`** (single fact → no structure): `+ must_not_contain: ["|"]`. Purely
  additive (never weakens); zero extra runtime (existing turn).

Suite counts updated: `clients/jyotech/golden/questions.yaml` header (60; company/docs 13 → 14)
and the `docs/TRACEABILITY.md` LLD-EVAL-01 row. `docs/LLD.md` line 159 states **59** — **not
edited here** (CLAUDE.md forbids editing LLD directly); flagged for the next doc pass below.

**Rider (b) — what stays live-check-guarded only.** The **spec-block** (verdict-first
recommendation) and the **two-part ORDERING** shapes are guarded by the live checks only, not by a
golden. A positive deterministic anchor for "verdict sentence appears first" or "part A before
part B" would require **OR-semantics in the runner's `expected_answer_contains` matching** (today
it is AND-only over fixed substrings, which cannot express "any one of these verdict phrasings" or
positional order robustly). Recorded as a **possible M7 eval enhancement — deliberately NOT built
here.** The pipe-negatives assert "not a table," which is the deterministically guardable half of
these shapes; the persona also permits a 2-column table for a recommendation, so a pipe-negative
cannot be applied to the spec-block shape at all.

### Rule-6 flag for the next doc pass (LLD not edited here)

- **LLD-EVAL-01 (docs/LLD.md line 159):** golden suite **59 → 60** — add `faq-founded-and-founder`
  (two-part ordered-prose shape guard; founder name + conflict-safe `199` year) and note
  `co-founded` strengthened to a single-fact no-table shape guard; company/docs count 13 → 14.
  The spec-block and two-part-ordering shapes remain live-check-guarded pending the OR-semantics
  eval enhancement (M7).

## 3. Gate results

- **`pytest -q`** — **363 passed** (incl. the 4 new after-sales template tests).
- **Hermetic fact/retrieval** over the full 60-question suite (old prompts) — **fact 46/46,
  retrieval 6/6**; the new `faq-founded-and-founder` founder fact resolves (`pass`).
- **Batch bring-up (LLD-EVAL-03):** `agentkit prompt load jyotech` created inactive versions for
  all 8 agents (load is unconditional). Activated the **7 changed** prompts; `deflect` left on the
  active **v1** (unchanged — no persona, not in the diff). Six activated `--layers fact,retrieval`
  (green each): `after_sales_intake.23`, `commercial_routing.23`, `documents_compliance.23`,
  `product_advisor.23`, `application_discovery.27`, `triage.27`.
- **FINAL activation — full three-layer gate (thinking-on, `LLM_DISABLE_THINKING=false`):**
  `agentkit prompt activate pv.jyotech.faq_company.27 --layers fact,retrieval,e2e` →
  **`Activated pv.jyotech.faq_company.27. Golden suite passed (fact+retrieval+e2e).`** (exit 0).
  This runs the whole suite through the real orchestrator + runtime LLM against the complete new
  active set — the definitive gate. **No triage routing regression** (every live check below routed
  correctly; the routing/outcome goldens are inside this gated suite and passed).
- **Standalone all-layer `agentkit eval run jyotech`** (definitive PASS record): see the fenced
  report below.

Active set after the gate (7 new + `deflect` v1):

```
after_sales_intake.23  application_discovery.27  commercial_routing.23  deflect.1
documents_compliance.23  faq_company.27  product_advisor.23  triage.27
```

Standalone eval report (`agentkit eval run jyotech`, all layers, thinking-on):

```
fact       46/46, na=14
retrieval  6/6, na=54
e2e        60/60
runtime: 1341.3s
RESULT: PASS
```

Zero failures, **zero flaky retries**. The two shape goldens are green end-to-end
(`co-founded` pass/na/pass, `faq-founded-and-founder` pass/na/pass); the presentation-shapes trio
(`prod-overview-industrial`, `prod-compare-process-natgas`, `prod-overview-industrial-hi`) and the
Hindi e2e pair (`e2e-hindi-hydrogen`, `e2e-hindi-price`) all pass. End state is e2e-green — the
definitive PASS.

## 4. Live checks (thinking-on unless noted; verbatim answers)

Run through the real orchestrator against the newly-active prompts (each in a rolled-back
savepoint, so no `ops.*` pollution). **Every turn routed correctly.**

**1. Single-fact ask** → plain sentences, no structure. `route=faq_company outcome=answered`.
> Jyotech is shown as established in 1991, with 1990 also appearing in some references. Would you
> like me to connect you with the team to confirm the exact incorporation year?

Two plain sentences, no table/list — the single-fact shape holds. (Honestly surfaces the
1990/1991 conflict. Citations were broad — 11 sources incl. `doc.catalogue_testing` — the
pre-existing over-citation / catalogue-testing pollution flagged in `presentation-shapes-notes.md`
§M7 follow-ups; a citation-precision issue, not a shape regression.)

**2. Duty recommendation** → verdict first, then spec block. `route=application_discovery
outcome=answered`, grounded on `fam.process_recip` / `cap.doc_jyotech_catalog_process_s005.0`.
> For your duty — 3,000 Nm³/hr of hydrogen from 20 to 350 bar, oil-free — that sits comfortably
> inside our **Process Compressors (Recip.)** range. The range is published up to 25,000 Nm³/hr
> and up to 1,000 barg, with oil-free non-lubricated construction and API-618 or equivalent design.
>
> Shall I have our engineers confirm the exact frame for your site?

Verdict-first spine intact; the supporting specs are here folded into one prose sentence (the
"lightest shape" the persona permits) rather than a discrete label-first block. The label-first
block DOES render on the Hindi duty (#6) and the thinking-off duty (#8), so the shape capability is
present; English chose prose this run (acceptable LLM shape variance, within the persona menu).

**3. Two-part question** → ordered passages. `route=faq_company outcome=answered`.
> Jyotech was founded by Mr. Deepak Bhatia. The founding year appears as 1991 in one place and
> 1990 in others. Would you like me to connect you with the team to confirm the exact year?

Both parts answered in plain prose, no table (matches the new golden: `199` + `Deepak Bhatia`
present, no `|`). The model answered founder-then-year (question asked year-then-founder) — the
golden guards two-part completeness + not-a-table, not strict ordering (rider (b)).

**4a. Variants ask** → small table (3+ variants). `route=product_advisor outcome=answered`,
per-variant family grounding (`fam.mch_engine_bac`, `fam.mch_smart_bac`, `fam.mch_ergo_bac`,
`fam.mch_silent_bac`).
> The **MCH-16** is available in an engine-driven model and several electric variants.
>
> | Variant | Drive | F.A.D. / pressure |
> |---|---|---|
> | **MCH-16 (Petrol / Diesel)** | 9-10 HP petrol or diesel engine | 265 lpm at 200/225 or 300/330 bar |
> | **MCH – 13 / 16 ELECTRIC (SMART SERIES)** | 4/5.5 kW 3-phase electric motor | 200-300 lpm at 200/225 or 300/330 bar |
> | **MCH – 13 / 16 Electric (ERGO Series)** | 4/5.5 kW 3-phase electric motor | 200-300 lpm at 200/225 or 300/330 bar |
> | **MCH – 13/16 & 21/23 (MARK3 SILENT / SUPER SILENT SERIES)** | 4/5.5/7.5 kW 3-phase electric motor | 200-380 lpm at 200/225 or 300/330 bar, optionally up to 420 bar |
>
> The **Smart** is the portable fire-tender option, **Ergo** is open-frame, and the **Mark3 Silent
> / Super Silent** is sound-proofed. Do you need engine-driven portability, or will 3-phase power
> be available at the site?

Unchanged table shape — exactly 3 columns (persona cap), number+unit kept together per cell.

**4b. Comparison** → attribute table. `route=product_advisor outcome=answered`, both compared
families grounded (`fam.process_recip`, `fam.natural_gas_recip` among the cited set).
> Here's how the two lines sit side by side:
>
> | Attribute | **Process Compressors (Recip.)** | **Natural Gas Compressors (Motor & Gas Engine Driven)** |
> |---|---|---|
> | Type | Reciprocating, non-lubricated/lubricated, water-cooled, vertical/V-type/W-type/horizontal | Reciprocating, lubricated, air-cooled, horizontal balanced opposed |
> | Capacity | up to 25,000 Nm³/hr | 10,000–100,000 SCMD |
> | Suction pressure | as per requirement | as per requirement |
> | Discharge pressure | up to 1,000 barg | up to 120 barg |
> | Application | H2, hydrocarbon gases, natural gas, BOG, mixed gas | upstream wellhead gas compressor, gas lift up compressor, midstream pipeline booster |
> | Standard | API-618 or equivalent | API-11P / ISO 13631 |
>
> The **Process Compressors (Recip.)** cover a much wider gas and pressure range, while the
> **Natural Gas Compressors (Motor & Gas Engine Driven)** are purpose-built for wellhead and
> pipeline duty. Which gas and pressure are you sizing for?

Unchanged 2-column comparison shape; rows = attributes with units inseparable; cells filled
per-product from tool results.

**5. Division overview** → 3–5 clusters, no spec dump. `route=product_advisor outcome=answered`.
> Our industrial compressor range covers a few main groups. **Process Compressors (Recip.)** and
> **Diaphragm Compressors** handle demanding process gases — oxygen, hydrocarbon, inert, natural
> gas and toxic gases. **CNG / biogas** machines cover compression, boosting and packaged duty for
> CNG and biogas applications. **Hydrogen Compressors** and **Hydrogen Fuelling Systems** cover
> hydrogen compression and fuelling. **MP/HP Air & Gas Compressors** and **MP/HP Air & Gas
> Boosters** handle medium- and high-pressure air and gas duties. And **Air Separation Plants** are
> available for air-based gas production. Which gas, flow and pressure are you looking at? I can
> point you to the right family.

~5 clusters, prose, no per-family model lists, no spec figures (no `25000`), closes with a
narrow-down invitation. Unchanged clustered orientation.

**6. Duty recommendation in Hindi** → verdict-first + spec block, register intact.
`route=application_discovery outcome=answered`, grounded on `fam.process_recip`.
> अच्छी बात — आपकी 3,000 Nm³/hr हाइड्रोजन, 20 से 350 bar, ऑयल-फ्र ज़रूरत हमारी **Process Compressors
> (Recip.)** श्रेणी में आराम से आती है; यह पूरी तरह दायरे के भीतर है।
>
> क्षमता: 25,000 Nm³/hr तक
> डिस्चार्ज: 1000 barg तक
> निर्माण: ऑयल-फ्र (नॉन-लुब्रिकेटेड)
> मानक: API-618 या समतुल्य
>
> क्या मैं हमारे इंजीनियरों से सटीक फ्रेम की पुष्टि करवा दूँ?

Verdict-first, then a label-first Devanagari spec block. **Register intact** inside the spec block:
the only Latin is the allowed three exceptions — the bold product name **Process Compressors
(Recip.)**, units (`Nm³/hr`, `bar`, `barg`) and the standard code `API-618`. Everything else is
written Hindi (क्षमता / डिस्चार्ज / निर्माण / मानक / नॉन-लुब्रिकेटेड). **Minor observation** (not a gate
failure, not a Latin leak): "ऑयल-फ्री" is truncated to "ऑयल-फ्र" (dropped ी) twice — a cosmetic
Devanagari compose glitch; `script_purity` does not flag it (it is Devanagari) and this turn is not
a `script_purity` golden. Noted as an LLM-variance cosmetic issue, no code/prompt change.

**7. After-sales completion in Hindi** → the new close template fires.
`route=after_sales_intake outcome=handoff`.
> धन्यवाद — मैंने कालकत्ता में आपके MCH-16 को नोट कर लिया है, और यह कि आपको सर्विस विज़िट चाहिए। आपके
> क्षेत्र को हमारा NOIDA कार्यालय (sales@jyotech.com) देखता है; मैं आपकी जानकारी उन्हें भेज देता हूँ और वे आपसे
> arnab@example.com पर संपर्क करेंगे।

The new `_CLOSE_TEMPLATES["hi"]` rendered exactly as designed: pure-Devanagari scaffolding with the
runtime values interpolated verbatim — model `MCH-16`, need `सर्विस विज़िट`, the office name/email
`NOIDA (sales@jyotech.com)`, and the contact `arnab@example.com` (model/email/product-name Latin
values ride correctly). **Observation (pre-existing, not introduced here):** the slot LLM extracted
`site_city` as Devanagari "कालकत्ता", which `get_office` did not resolve to the Kolkata branch, so it
fell back to the **head office (NOIDA)** (the LLD-TOOL-06 head-office fallback). The template is
correct; the wrong-office naming is a `get_office` Devanagari city-matching gap exposed by the
Hindi path — flagged as a follow-up below, out of scope for this bounded task.

**8. Duty recommendation with `LLM_DISABLE_THINKING=1`** (record, not gated).
`route=application_discovery outcome=answered`, same grounding as #2; turn ~fast (≈compose 7 s
class). Reproduce: `LLM_DISABLE_THINKING=1 agentkit ...` / the harness with the env var set.
> Good news — that duty sits comfortably inside our **Process Compressors (Recip.)** range; your
> 3,000 Nm³/hr at 350 bar, oil-free, is well within envelope.
>
> Capacity: up to 25,000 Nm³/hr
> Discharge: up to 1,000 barg
> Build: oil-free (non-lubricated)
> Standard: API-618 or equivalent
>
> Shall I have our engineers confirm the exact frame for your site?

With thinking off, the English duty produced a clean verdict-first **label-first spec block**
(cleaner than the thinking-on prose in #2), correctly grounded, faster. No degradation on this
single turn — consistent with the notes' finding that thinking-off failures are a rotating,
suite-level phenomenon, not deterministic per turn. `LLM_DISABLE_THINKING` remains **default off**
(the certified gate config); this was a record-only observation.

## 5. Deviations from the drafted prompts

**None.** The externally-authored prompt drafts passed the full three-layer gate as-authored — no
surgical edit to any prompt was required. The only code written this session was the one authorised
build item (the after-sales close templates + tests) and the two approved goldens.

### Follow-up observations (out of scope for this bounded task)

1. **`get_office` does not match Devanagari city names.** A Hindi after-sales completion whose
   `site_city` slot is Devanagari ("कालकत्ता") falls through to the head-office fallback (NOIDA)
   instead of naming the correct branch (Kolkata). The new close template is correct; the gap is in
   `get_office` city resolution. Candidate fix (M6/M7): transliterate or alias-map the city slot
   before `get_office`, or resolve offices on a script-insensitive key.
2. **faq_company over-citation / `catalogue_testing` pollution** persists on single-fact and
   two-part company questions (thinking-on) — already logged as the M7 follow-up in
   `presentation-shapes-notes.md`; a citation-precision issue, not a shape or routing regression.
3. **Devanagari spec-word truncation** ("ऑयल-फ्री" → "ऑयल-फ्र") observed once in the Hindi duty —
   cosmetic compose variance, not a register (Latin-leak) failure.
