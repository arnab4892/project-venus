---
title: Hindi (Devanagari) response support — persona register, per-language fixed strings, digit-fold safety net
date: 2026-09-03
author: Claude Code (paired with arnab.sharma)
type: implementation note (prompt-layer + defensive code + goldens; no PRD/HLD/LLD design change)
lld_items: [LLD-RT-05, LLD-RT-07, LLD-EVAL-01, LLD-EVAL-02]
prd_row: PRD-F-012
branch: hindi-devanagari-support
---

# Hindi (Devanagari) response support

Completes the Devanagari-Hindi half of PRD-F-012 (which already commits to English / Hindi /
Hinglish). **No PRD/HLD/LLD design change** — the requirement and the runtime plumbing already
exist: `runtime/language.py` maps `hi → "Hindi"`, `respond_in("hi")` steers the LLM, `english_query`
already routes `hi` retrieval, and `triage.md` already lists `hi`. This change adds the persona
guidance that tells the agents *how* to write mixed-script Hindi, makes the fixed customer-facing
strings language-aware, and adds a defensive digit-fold safety net + goldens.

## What changed

### 1. Persona + exemplars (prompt layer)
- `clients/jyotech/prompts/_persona.md`: new `Language (Hindi — Devanagari):` register section.
  **Register (as hardened after a live-transcript pass — the first cut mirrored Hinglish and read as
  "Hinglish-in-Devanagari"):** the reply is **written Hindi throughout**, technical vocabulary
  transliterated as standard written Hindi does (कंप्रेसर, गैस, प्रेशर, कैपेसिटी). **Only three things
  stay Latin** — (1) exact product/family/model/brand names as printed in the catalogue (bold,
  matching the Sources panel), (2) units (Nm³/hr, bar, barg, kW, HP, lpm, …), (3) standard codes
  (API-618, ISO, EN, NFPA). Ordinary English words in Latin are forbidden; figures stay ASCII digits,
  Indian-grouped (`25,000`).
- One Devanagari exemplar added to each of the six customer-facing agent prompts
  (application_discovery, product_advisor, documents_compliance, after_sales_intake,
  commercial_routing, faq_company), mirroring the existing English/Hinglish blockquote pairs and
  the `NN,NNN` placeholder-figure convention.
- `clients/jyotech/prompts/triage.md`: explicit clause — **Devanagari script → `hi`; romanised →
  `hinglish`** — so classification is deterministic, not merely inferred from the label "Hindi".

### 2. Fixed customer-facing strings → per-language templates (LLD-RT-07)
Fixed strings that bypass `respond_in` (they ship as literal text, not LLM-composed) now select a
**deterministic, pre-vetted en/hi/hinglish template** by the turn's detected language, mirroring the
existing `commercial_routing.py::_TEMPLATES` pattern. They are **never LLM-translated at runtime** —
several of them ship precisely when a model call has just failed, so the safety net must be
deterministic. **Hinglish variants were also missing at these sites and were added in the same pass.**

| Site | Trigger | Notes |
|---|---|---|
| `runtime/grounding.py` `_FALLBACK_TEXTS` (`ground_answer(language=…)`) | grounding gate strips a draft | `FALLBACK_TEXT` kept as the `en` alias; orchestrator `n_ground` passes `wf.triage.language` |
| `runtime/orchestrator.py` `_CLARIFY_TEXTS` | low-confidence triage → clarify | `CLARIFY_MESSAGE` kept as `en` alias |
| `runtime/orchestrator.py` `_HICCUP_TEXTS` | runtime LLM returns junk 3× → degrade | `LLM_HICCUP_MESSAGE` kept as `en` alias; language falls back to `en` when triage itself failed |
| `runtime/agents/application_discovery.py` `_NOMATCH_TEMPLATES` / `_NOMATCH_NOTE` | duty matches no published family → handoff stub | visitor's own gas/capacity/pressure stay English |
| `runtime/agents/product_advisor.py` `_PRICE_HANDOFF_TEXTS` | product price / lead-time ask → handoff | no figure in any language |

### 3. Devanagari digit-fold safety net (defensive)
Devanagari digits ०-९ (U+0966–U+096F) fold 1:1 to ASCII, so `str.translate` preserves offsets. The
ASCII-digit persona rule is the intended behaviour; this is the net so a Devanagari figure can never
slip past the numeric guard or the eval matcher unnoticed.
- `runtime/grounding.py`: `_fold_digits` applied in `_all_numbers`, `_spec_numbers`,
  `redact_unsourced_spec_numbers` (redaction output normalises to ASCII, offsets intact).
- `eval/runner.py`: `normalize_digits` folds Devanagari before the comma / trailing-zero passes.

### 4. Goldens (LLD-EVAL-01)
Two **e2e-only** Hindi questions, each with a Devanagari-script anchor so an English/Hinglish reply
fails (the single-turn way to assert the reply came out in Devanagari):
- `e2e-hindi-hydrogen` (capability) — mirrors `e2e-hinglish-hydrogen`: substrings `["process",
  "recip"]` + anchor `है` (highest-frequency Hindi copula, near-certain in a *generated* reply),
  `must_not ["20000","near-edge"]`, no figure. The English `cap-hydrogen-process` owns the 25000
  guard; digit-folding has its own unit tests.
- `e2e-hindi-price` (product) — mirrors `prod-price-handoff`: handoff, `must_not` currency tokens,
  anchor `जोड़` (a substring present in *both* the commercial_routing and product_advisor `hi`
  handoff templates, so it holds whichever path triage picks — a deterministic reply, not generated).

Suite header + section subheaders recounted: **53 → 55** (capability 20, product 14, company/docs 13,
after-sales 6, out-of-scope 2). The stale per-section subheaders were corrected to true counts in
the same pass.

## Hardcoded-string sweep — English-only residuals (documented, not silent)

The runtime was swept for every customer-facing hardcoded string. The five in §2 were converted.
The following **remain English-only** and are recorded here with their trigger conditions — none is
silent. Their normal path is already in-language (the LLM composes the reply via `respond_in`); the
residual only surfaces on the noted condition. **Recommended follow-up, highest value first:**

1. **`runtime/agents/after_sales_intake.py` completion close** (`msg`, ~line 91) — **always ships
   on after-sales completion**, interpolating office/model/city/need/contact. This is the only
   residual that fires on a *normal* path; strongest candidate to convert next.
2. **`runtime/agents/after_sales_intake.py` `_contact_question`** (~line 32) — deterministic
   contact-step phrasing; fires on the contact-detail slot in every after-sales flow.
3. **`runtime/agents/after_sales_intake.py` `_SLOT_QUESTIONS`** (~line 22) — fallback only, shown
   when the slot LLM returns an empty `message`; the normal in-language question comes from the LLM.
4. **`runtime/agents/application_discovery.py` `_SLOT_QUESTIONS`** (~line 30) — same: LLM-empty
   fallback only.

Not members of the class: `deflect.py` (reply is LLM-composed via `respond_in`; empty-string on
failure is a separate degradation, not an English string) and `documents_compliance._document_card`
(a structured card payload — title from the document, no prose).

### Follow-up: the `hi` fixed templates predate the tightened register — **RESOLVED in v2 (below)**
The five per-language templates converted in §2 were written under the *first* Hindi register (which
mirrored Hinglish), so their `hi` variants were themselves **Hinglish-in-Devanagari** — they carried
Latin loanwords outside the three exceptions (`engineers`, `commercial team`, `tailored solution`,
`lead time`, `compressor range`, and the clarify/hiccup lists `gas`/`flow`/`discharge pressure`).
The register-tightening pass was scoped **prompt-layer only**, so these deterministic code strings
were left as-is. **The v2 register-purity pass (below) re-authored all five to the written-Hindi
three-exception rule and now asserts each one is register-pure with the same checker the eval gate
uses.** This section is retained for history; the follow-up is closed.

---

# v2 — register-purity pass (2026-09-03)

Makes Devanagari replies **pure written Hindi**, and closes the deferred fixed-template follow-up.
Prompt-layer + code + a new opt-in eval check; **no data, tool, routing or gate-semantics change**.

## 1. Persona (`_persona.md`, Hindi section)
- **Dropped the enumerated forbidden-word list** ("Never write available/suitable/broad/…"). Naming
  the forbidden words reinforced them; the rule is now stated positively — *outside the three
  exceptions, every word is Hindi*. The transliteration examples stay, explicitly marked **"for
  example"** (register, not a fixed vocabulary).
- **Added a tool-vocabulary translation rule + glossary:** the tool results are English; a Hindi
  reply **translates** their vocabulary, never copies the English words —
  capacity → क्षमता · discharge pressure → डिस्चार्ज प्रेशर · suction → सक्शन प्रेशर ·
  published / catalogue → कैटलॉग में प्रकाशित · near the upper edge → ऊपरी सीमा के करीब ·
  "shall I connect you with our engineers" → "क्या मैं आपको हमारे इंजीनियरों से जुड़वा दूँ?".

## 2. Exemplar audit
All six Devanagari exemplars were audited against the checker (§4) — **zero offenders**; they were
already register-pure from the register-tightening commits (`a802a7f`, `94ea8c9`) and each already
carries a bold Latin product name inside otherwise-pure Hindi. No exemplar rewrite was needed.

## 3. Recency reminder (`runtime/language.py::respond_in`)
For `hi`, the bare `"Respond in Hindi."` is replaced by a **two-line register reminder** (written
Devanagari throughout; Latin only for product names in bold, units and standard codes; ASCII
digits) so the rule is the **last** thing the compose model reads. `en`/`hinglish` unchanged.

## 4. Generic script-purity check (`eval/runner.py`)
New opt-in per-question flag **`script_purity: hi`**. After stripping inline `[tr1]` citation
markers, `**bold**` spans, `{format placeholders}`, standard codes and unit tokens, **any remaining
`[A-Za-z]{2,}` run fails the question and is named** in the report. Threshold zero — strict by
design; the e2e retry-once policy absorbs a one-off wobble, a systematic leak blocks the gate with
exact diagnostics. Enabled on the two e2e-hindi goldens (no word-needle `must_not`s). The function
`script_purity_offenders` is the **single implementation** used by both the eval gate and the
fixed-template unit tests.

## 5. Fixed `hi` templates re-authored (closes the §2/follow-up debt)
All five `hi` variants rewritten to the three-exception rule; each now asserts register-pure via the
shared checker in its existing per-language test. `hinglish` variants re-read for quality — left
unchanged (Latin is their register). Before → after (`hi`):

| Site | Before (leak) | After |
|---|---|---|
| `grounding._FALLBACK_TEXTS` | …हमारे **engineers** से जोड़ देता हूँ… | …हमारे **इंजीनियरों** से जोड़ देता हूँ… |
| `orchestrator._CLARIFY_TEXTS` | …किसी **specific gas/duty** के लिए **compressor**, **product**…, **company**/**document**…, **service** और **spares** | …किसी **ख़ास गैस/ड्यूटी** के लिए **कंप्रेसर**, किसी **उत्पाद**…, **कंपनी**/**दस्तावेज़**…, **सर्विस** और **स्पेयर पार्ट्स** |
| `orchestrator._HICCUP_TEXTS` | …जो **gas**, **flow** और **discharge pressure** चाहिए… | …जो **गैस**, **फ़्लो** और **डिस्चार्ज प्रेशर** चाहिए… |
| `application_discovery._NOMATCH_TEMPLATES` / `_NOMATCH_NOTE` | **published compressor range**…**cover**…**engineers**…**tailored solution**…**related machines**…**published data**…**duty**…**compare** | **प्रकाशित कंप्रेसर श्रेणी**…**कवर**…**इंजीनियरों**…**अनुकूलित समाधान**…**संबंधित मशीनें**…**प्रकाशित डेटा**…**ड्यूटी**…**तुलना** |
| `product_advisor._PRICE_HANDOFF_TEXTS` | …कीमत या **lead time**…हमारी **commercial team**…एक **accurate quotation**… | …कीमत या **डिलीवरी समय**…हमारी **कमर्शियल टीम**…**सटीक कोटेशन**… |

`commercial_routing._TEMPLATES["hi"]` was already pure (both team fills); a purity assertion was
added for it too, since it is the other handoff path the `e2e-hindi-price` golden may take. The
`{gas}`/`{capacity_unit}` slots in the no-match template still interpolate the visitor's own English
technical values — that is unchanged and out of scope; the checker strips `{…}`, so the test covers
the hand-authored prose.

## v2 gate
`pytest -q`: **324 passed** (+17: `respond_in` selection, `script_purity_offenders` unit + e2e
wiring, and five fixed-template purity assertions).

Bring-up per LLD-EVAL-03: `prompt load` (8 versions; triage/deflect bodies unchanged, left inactive)
→ five persona agents activated through the fast `fact,retrieval` gate → the sixth
(`application_discovery.19`) activated through the **full `fact,retrieval,e2e` gate over the whole
55-question suite** (25m46s), which passed green **including both `script_purity: hi` goldens**
(`e2e-hindi-hydrogen`, `e2e-hindi-price`). All six persona agents are now active on the tightened
register. (The activation gate is itself a full all-layer eval run; a redundant standalone repeat
was not run.)

**Live register checks (through the now-active prompts, in a rolled-back savepoint):**
- "…लगभग 30 kg/hr क्षमता और 800 bar तक…हाइड्रोजन कंप्रेसर…" → pure Hindi, correct published figures
  **40 Kg/hr / 850 barg**, "यह ड्यूटी **ऊपरी सीमा के करीब** है" (the glossary phrasing), zero offenders.
- "MCH-16 की कीमत क्या है?" → routed to the (already-pure) commercial handoff template, `जोड़` present,
  zero offenders.
- The hydrogen duty golden question, re-run 3×, returned full register-pure answers each time
  (**Process Compressors (Recip.)** in bold; **क्षमता / डिस्चार्ज प्रेशर / प्रकाशित** from the glossary;
  25,000 Nm³/hr · 1,000 barg · API-618), `script_purity` offenders `[]` every run. One earlier run
  produced a sparse (closing-question-only) answer — the known ~1-per-run live-LLM content transient
  the e2e retry-once policy absorbs, not a register regression (register stayed pure throughout).

## Rule-6 flags (for the next doc pass)
- **LLD-RT-07**: add the Hindi (Devanagari) register sentence (parallel to the Hinglish one) and note
  that the fixed fallback / clarify / hiccup / no-match / price-handoff strings are per-language
  (en/hi/hinglish) fixed templates selected in code — never runtime-LLM-translated. Note the four
  documented English-only residuals above.
- **LLD-RT-05**: `ground_answer` now takes `language` and ships the fallback sentence in the visitor's
  language.
- **LLD-EVAL-01**: golden suite 53 → 55 (Hindi pair).

---

# v2.1 — symmetric reply-language instruction + exemplar language tags + direction goldens

A live regression on the prompt set **this branch activated** (turn `b8b63a4f`): an **English**
conversation (triage `language=en`, confidence 0.98) was answered **in Hinglish**, near-verbatim
reciting the `application_discovery` Hinglish exemplar. Root cause: v2 strengthened `respond_in("hi")`
into a firm last-position register reminder and added vivid Hindi/Hinglish exemplars about the exact
hydrogen scenario, but left `respond_in("en")` as one bare sentence — so **exemplar imitation now
outweighed the English instruction**. Three fixes, all prompt-layer + one code touch; no data, tool,
routing or gate-semantics change.

## 1. Symmetric `respond_in` (`runtime/language.py`)
`en` and `hinglish` now get a **firm last-position instruction of equal strength to `hi`**, and all
three close with the **same shared clause** — *examples illustrate structure and format only; the
reply language comes ONLY from this instruction*:
- **en**: "Reply in English only. Do not use Hindi or Hinglish words or phrasing. …"
- **hinglish**: "Reply in Hinglish — romanised Hindi/English in Latin script … No Devanagari, and do
  not drift into pure English. …"
- **hi**: the v2 two-line written-Devanagari register reminder, now closing with the same tail.

Unit-tested (`tests/runtime/test_language.py`): all three strings, the shared tail on every one, the
case-insensitive/`None`/unknown → English selection.

## 2. Exemplar language tags (all six agent prompts + persona)
Every worked example is now labelled with the language it demonstrates — *"English example (use this
style ONLY when replying in English) —"*, and likewise Hinglish / Devanagari Hindi (19 headers,
including `application_discovery`'s second English table example). `_persona.md` states it once, for
all agents: **imitate only the example whose language matches the reply-language instruction; the
other-language examples show structure, never language.**

## 3. Language-direction goldens (closing the gate blind spot)
The regression passed the gate because English e2e questions assert English technical terms — a
Hinglish reply still contains "compressor", "hydrogen", etc. Added romanised-Hindi function-word
`must_not_contain` markers (`aapki`, `aapke`, `aapko`, `hamari`, `chahiye`, `kya main` — impossible in
an English reply or any product name) to **three English e2e questions**, one per agent family:
`cap-hydrogen-process` (capability), `prod-mch16-specs` (product), `co-certifications` (company/FAQ).
The Devanagari direction was already guarded by `script_purity`; **every reply language now has a
golden asserting the other directions do not appear uninvited.**

## Process observation (for the next doc pass — README/ops rule)
This branch's session **activated its prompt versions into the shared `ops` DB before the branch
merged**, so the regression was **live under a `main` checkout** — *"unmerged" does not mean "not
live"*. Proposed rule for `docs`/`README`: **a feature-branch session that activates prompts either
re-activates the prior version set before it stops, or the branch merges promptly — activation from a
branch carries a rollback obligation.** (Flagged here; the doc edit is a rule-6 follow-up, not made
in this prompt-layer change.)

## v2.1 gate & live checks
`pytest -q`: **325 passed** (`test_language.py` rewritten for the three firm strings + shared tail;
`test_hinglish.py` updated to the firm English string).

Bring-up per LLD-EVAL-03: `prompt load` (8 versions; triage/deflect bodies byte-identical to active,
left inactive) → five persona agents activated through the fast `fact,retrieval` gate →
`application_discovery.20` activated through the **full `fact,retrieval,e2e` gate over the whole
55-question suite**, green — **including the three romanised-Hindi direction guards
(`cap-hydrogen-process`, `prod-mch16-specs`, `co-certifications`) and both `script_purity: hi`
goldens**. Then a **standalone all-layer `agentkit eval run`: RESULT PASS, e2e 55/55** (one unrelated
retry-flaky — `cap-natgas-unit-conversion` family-name surfacing on the first pass, `cap-process-max-capacity`
on the second; both content variance in family selection, not register/direction, and both green on
retry under the LLD-EVAL-02 policy).

**Live direction checks (all three, through the now-active v2.1 prompts):**
- **English (two-turn hydrogen)** → both turns fully English (turn 1 application_discovery, turn 2
  product_advisor), zero romanised-Hindi markers, zero Devanagari — the regression that opened this
  scope is fixed.
- **Hindi (30 kg/hr hydrogen)** → pure written Devanagari, `script_purity` offenders `[]`, correct
  published figures (**40 kg/hr / 850 barg**), the glossary phrasing ("ड्यूटी … **ऊपरी सीमा के … करीब**",
  "क्या मैं आपको हमारे इंजीनियर्स से जोड़ दूँ?"), a bold Latin **Hydrogen Compressors** inside otherwise-pure
  Hindi. (The live LLM produced a couple of Devanagari-spelling wobbles — कप्सिटी/प़ेशर — which are
  script-internal transliteration transients, not Latin leaks; register stayed pure.)
- **Hinglish (MCH-16)** → romanised Latin, no Devanagari, genuine Hinglish register markers
  (aapke/aapko/chahiye) — Hinglish, not pure English.

Every response language now verified in both directions: it appears when invited (live check) and is
guarded against appearing uninvited (script_purity for Devanagari; the romanised `must_not` markers
for Hinglish; the English e2e goldens for English).
