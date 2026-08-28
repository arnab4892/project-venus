You are proposing the **product family** list for Jyotech Engineering from its
public content. A *family* is the unit the website actually describes — a class
of compressor or equipment (e.g. "Process Gas Compressor", "Oxygen Compressor",
"MCH Series Breathing Air Compressor", "Air Lifting Bags"), not an individual
model number.

You are given a digest of the classified sections (capability specs, product
lists, company and office sections) across the whole corpus. Propose the complete
family list. Return JSON `{ "families": [ ... ] }` where each family is:

- `id` — a stable slug, lower-case, prefixed `fam.` (e.g. `fam.process_recip`,
  `fam.oxygen_recip`, `fam.mch_bac`, `fam.lifting_bags`).
- `division` — one of `industrial`, `fire_rescue`, `diving`.
- `category` — the website's menu-level grouping (e.g. "Process Gas Compressors",
  "Breathing Air Compressors", "Rescue Equipment").
- `name` — the family's display name, as the site words it.
- `summary` — one or two sentences, derived from the content (no marketing spin).
- `source` — where you saw it (a `doc.*` id and/or heading).

Rules:
- Group by what the content describes; merge duplicates that appear in both a web
  page and the catalogue PDF into a single family.
- Do not invent families that the content does not support.
- This list will be human-reviewed and then frozen; extractors will only be
  allowed to assign ids that appear here.
