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

    return {"query": model_or_family, "matched_by": None, "products": []}
