"""``get_product`` — LLD-TOOL-03.

Look up products by model number or family (id or name), joined to their family. Model
matching is alias-normalised so ``MCH16`` == ``MCH-16`` (spaces/hyphens stripped). A product
with no printed ``model_name`` is presented by its family name + variant/description
(LLD-EXT-06) — never a blank or an invented name.
"""

from __future__ import annotations

import re

from sqlalchemy import Connection, text

_ALIAS_STRIP_RE = re.compile(r"[\s\-]+")


def normalise_alias(value: str) -> str:
    """Strip spaces/hyphens and case-fold for model-number matching (``MCH-16`` → ``mch16``)."""
    return _ALIAS_STRIP_RE.sub("", value).lower()


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
        "category": row["category"],
        "division": row["division"],
        "source_doc_id": row["source_doc_id"],
        "source_locator": row["source_locator"],
    }


def get_product(conn: Connection, model_or_family: str) -> dict:
    """Return ``{"query", "matched_by", "products": [...]}`` for a model or family lookup.

    Tries an exact alias-normalised model match first; failing that, a family match by id or
    name (returning every product in the family). Each product carries a runtime display name.
    """
    key = normalise_alias(model_or_family)
    rows = conn.execute(text(_SQL)).mappings().all()

    by_model = [r for r in rows if r["model_name"] and normalise_alias(r["model_name"]) == key]
    if by_model:
        return {"query": model_or_family, "matched_by": "model", "products": [_as_product(r) for r in by_model]}

    by_family = [
        r
        for r in rows
        if normalise_alias(r["family_id"]) == key
        or (r["family_name"] and normalise_alias(r["family_name"]) == key)
    ]
    if by_family:
        return {"query": model_or_family, "matched_by": "family", "products": [_as_product(r) for r in by_family]}

    return {"query": model_or_family, "matched_by": None, "products": []}
