"""``get_product`` — LLD-TOOL-03.

Look up products by model number or family (id or name), joined to their family. Model
matching is alias-normalised so ``MCH16`` == ``MCH-16`` (spaces/hyphens stripped). A product
with no printed ``model_name`` is presented by its family name + variant/description
(LLD-EXT-06) — never a blank or an invented name.
"""

from __future__ import annotations

from agentkit.runtime.tracing import observe

import re

from sqlalchemy import Connection, text

_ALIAS_STRIP_RE = re.compile(r"[\s\-]+")
_MODEL_PREFIX_RE = re.compile(r"[a-z]+")
_MODEL_NUM_RE = re.compile(r"\d+")


def normalise_alias(value: str) -> str:
    """Strip spaces/hyphens and case-fold for model-number matching (``MCH-16`` → ``mch16``)."""
    return _ALIAS_STRIP_RE.sub("", value).lower()


def _model_tokens(name: str | None) -> tuple[str, frozenset[int]]:
    """Split a model string into its series prefix + set of model numbers.

    "MCH-16" → ("mch", {16}); "MCH – 13 / 16 ELECTRIC" → ("mch", {13, 16}); "ICON / MCH6" →
    ("icon", {6}). Used to match a queried model against its sibling variants: the same series
    (leading letters) sharing at least one model number (so "MCH-16" reaches the "13/16" range).
    """
    low = (name or "").lower()
    m = _MODEL_PREFIX_RE.match(low)
    prefix = m.group(0) if m else ""
    nums = frozenset(int(n) for n in _MODEL_NUM_RE.findall(low))
    return prefix, nums


def product_display_name(
    model_name: str | None, family_name: str | None, variant: str | None, description: str | None
) -> str:
    """Runtime display name (LLD-EXT-06): model if printed, else family + variant/description."""
    if model_name:
        return model_name
    tail = variant or (description.strip().split("\n")[0][:60] if description else None)
    base = family_name or "product"
    return f"{base} — {tail}" if tail else base


_SQL = (
    "SELECT p.product_id, p.family_id, p.model_name, p.variant, p.description, p.attributes, "
    "       p.source_doc_id, p.source_locator, "
    "       pf.name AS family_name, pf.category, pf.division, pf.summary AS family_summary "
    "FROM facts.active_product p "
    "JOIN facts.active_product_family pf ON pf.family_id = p.family_id "
    "ORDER BY p.product_id"
)

# The family registry + its published envelope (capability rows). Read straight from the family
# tables so a family carrying capability rows but NO product rows (the process/gas families) is
# still resolvable — the product-anchored ``_SQL`` above cannot see it. Same tables
# ``match_capability`` reads; figures returned verbatim.
_FAMILY_SQL = (
    "SELECT family_id, name AS family_name, category, division, summary, "
    "       source_doc_id, source_locator "
    "FROM facts.active_product_family ORDER BY family_id"
)
_CAP_SQL = (
    "SELECT cap_id, comp_type, lubricated, cooling, capacity_min, capacity_max, capacity_unit, "
    "       discharge_p_min, discharge_p_max, pressure_unit, standards, driver, "
    "       source_doc_id, source_locator "
    "FROM facts.active_capability_row WHERE family_id = :fid ORDER BY cap_id"
)
_CAP_GAS_SQL = "SELECT gas FROM facts.active_capability_gas WHERE cap_id = :cid ORDER BY gas"


def _as_product(row) -> dict:
    return {
        "product_id": row["product_id"],
        "family_id": row["family_id"],
        "display_name": product_display_name(
            row["model_name"], row["family_name"], row["variant"], row["description"]
        ),
        "model_name": row["model_name"],
        "variant": row["variant"],
        "description": row["description"],
        "family_name": row["family_name"],
        "family_summary": row["family_summary"],
        "category": row["category"],
        "division": row["division"],
        "attributes": row["attributes"],
        "source_doc_id": row["source_doc_id"],
        "source_locator": row["source_locator"],
    }


@observe(name="get_product", as_type="tool")
def get_product(conn: Connection, model_or_family: str) -> dict:
    """Return ``{"query", "matched_by", "products": [...]}`` for a model or family lookup.

    Tries an exact alias-normalised model match first; failing that, a family match by id or
    name (returning every product in the family). Each product carries a runtime display name.
    """
    key = normalise_alias(model_or_family)
    rows = conn.execute(text(_SQL)).mappings().all()

    # Model matches, ranked: an exact alias match first, then sibling VARIANTS — products in the
    # same series (leading letters) that share the queried model number (so "MCH-16" also returns
    # the "MCH-13/16" electric/silent variants, not just the exact one). A query with no model
    # number (e.g. "CCDU") has no siblings and behaves as before.
    q_prefix, q_nums = _model_tokens(model_or_family)
    ranked: list[tuple[tuple, object]] = []
    for r in rows:
        if r["model_name"] and normalise_alias(r["model_name"]) == key:
            ranked.append(((0, 0, 0, r["product_id"]), r))  # exact match — the lead
            continue
        if q_prefix and q_nums:
            for field in (r["model_name"], r["variant"]):
                p, nums = _model_tokens(field)
                overlap = q_nums & nums
                if p == q_prefix and overlap:
                    # rank: more shared numbers first, then the more specific (fewer) model
                    ranked.append(((1, -len(overlap), len(nums), r["product_id"]), r))
                    break
    if ranked:
        ranked.sort(key=lambda t: t[0])
        seen_ids: set = set()
        products = []
        for _, r in ranked:
            if r["product_id"] not in seen_ids:
                seen_ids.add(r["product_id"])
                products.append(_as_product(r))
        matched_by = "model" if ranked[0][0][0] == 0 else "model_variant"
        return {"query": model_or_family, "matched_by": matched_by, "products": products}

    by_family = [
        r
        for r in rows
        if normalise_alias(r["family_id"]) == key
        or (r["family_name"] and normalise_alias(r["family_name"]) == key)
    ]
    if by_family:
        return {"query": model_or_family, "matched_by": "family", "products": [_as_product(r) for r in by_family]}

    # Containment fallback (LLD-TOOL-03, rule-6 flag): a caller often passes the model/family
    # name embedded in a phrase ("battery powered combi-tool FOR RESCUE", "C-Monitor FOR DIVING").
    # After exact matches fail, match a product/family whose (≥4-char) normalised name is a
    # substring of the normalised query — longest name wins, so a specific model beats a generic
    # family. This never fabricates: it still requires the published name to appear in the query.
    def _contains(name: str | None) -> bool:
        n = normalise_alias(name or "")
        return len(n) >= 4 and n in key

    contained = [
        (len(normalise_alias(r["model_name"] or "")), "model", r)
        for r in rows if r["model_name"] and _contains(r["model_name"])
    ] + [
        (len(normalise_alias(r["family_name"] or "")), "family", r)
        for r in rows if r["family_name"] and _contains(r["family_name"])
    ]
    if contained:
        best_len = max(c[0] for c in contained)
        winners = [c for c in contained if c[0] == best_len]
        kind = winners[0][1]
        if kind == "family":
            fam_id = winners[0][2]["family_id"]
            prods = [r for r in rows if r["family_id"] == fam_id]
        else:
            prods = [c[2] for c in winners]
        return {
            "query": model_or_family, "matched_by": f"{kind}_contains",
            "products": [_as_product(r) for r in prods],
        }

    # Family-envelope fall-through (LLD-TOOL-03 ext, rule-6 flag): the process/gas families carry
    # capability rows (facts.capability_row, FK to the family — not the product) but NO facts.product
    # rows, so every branch above (all product-anchored) misses them. Resolve the name against the
    # full family registry with the SAME rules — alias-exact on family id/name, then containment —
    # and return the family's published envelope taken verbatim from its capability rows, force-
    # citable by family + cap ids. Product-backed lookups returned earlier, so this is purely
    # additive: a name that resolved to products never reaches here.
    envelope = _family_envelope(conn, model_or_family, key)
    if envelope is not None:
        return envelope

    return {"query": model_or_family, "matched_by": None, "products": []}


def _family_envelope(conn: Connection, model_or_family: str, key: str) -> dict | None:
    """Resolve a family by name/id and return its published envelope, or ``None`` if no family.

    Reuses the model/family matching rules: an alias-exact match on ``family_id`` or ``family_name``
    first, then the same ≥4-char longest-wins containment as the product branch. A family that
    resolves but has zero capability rows returns a well-defined empty envelope (``capabilities:
    []``) — never ``None`` — so the caller can still name and cite it.
    """
    fams = conn.execute(text(_FAMILY_SQL)).mappings().all()
    hit = next(
        (
            f for f in fams
            if normalise_alias(f["family_id"]) == key
            or (f["family_name"] and normalise_alias(f["family_name"]) == key)
        ),
        None,
    )
    if hit is None:  # same containment rule as the product branch (≥4 chars, longest name wins)
        contained = [
            f for f in fams
            if f["family_name"] and len(normalise_alias(f["family_name"])) >= 4
            and normalise_alias(f["family_name"]) in key
        ]
        if contained:
            hit = max(contained, key=lambda f: len(normalise_alias(f["family_name"])))
    if hit is None:
        return None

    capabilities = []
    for c in conn.execute(text(_CAP_SQL), {"fid": hit["family_id"]}).mappings():
        gases = [g["gas"] for g in conn.execute(text(_CAP_GAS_SQL), {"cid": c["cap_id"]}).mappings()]
        capabilities.append({**dict(c), "gases": gases})
    return {
        "query": model_or_family,
        "matched_by": "family_envelope",
        "products": [],
        "family": {
            "family_id": hit["family_id"],
            "family_name": hit["family_name"],
            "category": hit["category"],
            "division": hit["division"],
            "summary": hit["summary"],
            "source_doc_id": hit["source_doc_id"],
            "source_locator": hit["source_locator"],
        },
        "capabilities": capabilities,
    }
