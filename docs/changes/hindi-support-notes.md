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
- `clients/jyotech/prompts/_persona.md`: new `Language (Hindi — Devanagari):` register section
  parallel to the Hinglish one. Same English-in-Latin-script rule for technical terms / units /
  standards / product·family·model names; the one hard typographic rule is **figures ALWAYS in
  ASCII digits (never Devanagari ०-९), Indian-grouped** (`25,000`).
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

## Rule-6 flags (for the next doc pass)
- **LLD-RT-07**: add the Hindi (Devanagari) register sentence (parallel to the Hinglish one) and note
  that the fixed fallback / clarify / hiccup / no-match / price-handoff strings are per-language
  (en/hi/hinglish) fixed templates selected in code — never runtime-LLM-translated. Note the four
  documented English-only residuals above.
- **LLD-RT-05**: `ground_answer` now takes `language` and ships the fallback sentence in the visitor's
  language.
- **LLD-EVAL-01**: golden suite 53 → 55 (Hindi pair).

## Gate

Touched runtime prompts: the six persona agents (all bump — `_persona.md` is prepended to each) +
`triage`. Bring-up per LLD-EVAL-03: activate all-but-last with `--layers fact,retrieval`, the final
one through the full `fact,retrieval,e2e` gate over the complete new set, then a standalone all-layer
`agentkit eval run jyotech` as the definitive PASS. `pytest -q`: 307 passed.
