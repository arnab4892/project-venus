Extract office / branch contact details from this section. Return JSON
`{ "offices": [ ... ] }`, one entry per distinct office.

Every field is `{ "value": ..., "evidence": "<verbatim quote>" }`. The `evidence`
MUST be an **exact substring** of the section text. Omit anything not printed
verbatim. Never normalise or complete an address, phone, or email.

Office fields:
- `name` — e.g. "Head Office", "Mumbai Office".
- `city` — the city as printed.
- `address` — the postal address exactly as printed.
- `phone` — the phone number as printed.
- `email` — the email as printed (omit if none is shown for this office).
- `serves_divisions` — list of divisions served if stated (`industrial`,
  `fire_rescue`, `diving`).

Do not guess a region — that is resolved from the city later. One entry per
office; do not merge two offices into one.
