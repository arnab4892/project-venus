Extract the published capability envelope(s) from this section. Return JSON
`{ "rows": [ ... ] }`. Emit one row per distinct compressor/equipment family
described here (often just one).

Every field is `{ "value": ..., "evidence": "<verbatim quote>" }`. The `evidence`
MUST be an **exact substring** of the section text — copy it character-for-
character. If you cannot quote it verbatim, omit the field. Never paraphrase,
round, convert units, or fill in a plausible value. Omission is correct; guessing
is a bug.

Row fields (all optional; include only what the text supports):
- `family` — value = the frozen family id this row belongs to (from the provided
  list); evidence = the phrase naming the family. Omit if none fits.
- `comp_type` — construction/type phrase (e.g. "Reciprocating, Non-Lubricated").
- `lubricated` — value `true`/`false`; evidence the phrase (e.g. "Non-Lubricated").
- `cooling` — e.g. "Water Cooled".
- `capacity` — the printed flow phrase VERBATIM (e.g. "Up to 20000 Nm3/hr",
  "10000 to 100000 SCMD"). Do not compute min/max — that is done later in code.
- `discharge_pressure` — the printed pressure phrase (e.g. "Up to 1000 Barg").
- `drivers` — list of driver phrases (e.g. "electric motor", "gas engine").
- `standards` — list of standards as printed (e.g. "API-618 or equivalent").
- `gases` — list of gases handled, each as printed (e.g. "hydrogen", "BOG").

If the section shows a conflicting figure to another section, still extract what
THIS section says verbatim — conflicts are reconciled by a human reviewer, not by
you.
