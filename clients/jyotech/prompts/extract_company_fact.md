Extract company facts from this section. Return JSON `{ "facts": [ ... ] }`, one
entry per distinct fact.

Every field is `{ "value": ..., "evidence": "<verbatim quote>" }`. The `evidence`
MUST be an **exact substring** of the section text. Omit anything you cannot quote
verbatim. Never infer or embellish.

Fact fields:
- `kind` — value = one of: `certification`, `founded`, `founder`, `facility`,
  `industry_served`, `client`, `coverage`, `contact`; evidence = the supporting
  phrase. Omit the fact if none of these kinds fit.
- `value` — the fact itself, as printed (e.g. "ISO 9001:2015", "1991",
  "Deepak Bhatia", "IOCL", "City Gas Distribution", "sales@jyotech.com").
- `detail` — an optional extra printed qualifier, if any.

One fact per certification, client, industry, etc. — do not bundle several into
one row.
