Extract the named products / models / kits from this section. Return JSON
`{ "products": [ ... ] }`, one entry per named model or variant.

Every field is `{ "value": ..., "evidence": "<verbatim quote>" }`. The `evidence`
MUST be an **exact substring** of the section text. If you cannot quote it
verbatim, omit the field. Never invent attributes or descriptions.

Product fields (include only what is printed):
- `family` — value = the frozen family id (from the provided list); evidence = the
  phrase naming the family. Omit if none fits (do not invent an id).
- `model_name` — the model exactly as printed, preserving casing and hyphens
  (e.g. "MCH-13/16", "NEPTUNE III", "PROEYE 951.S").
- `variant` — a variant/qualifier if printed (e.g. "SMART", "MARK3 SILENT",
  "high pressure").
- `description` — a short verbatim-derived description, only if the text gives one.
- `attributes` — a list of printed attribute phrases (e.g. "portable",
  "electric or engine driven", "fill rate 100 lpm"). Only what is literally
  printed as text — never read values out of an image or a picture caption.
