"""``list_products`` — LLD-TOOL-02.

List the families in a division (optionally filtered by category), each with its products.
Products are presented by their runtime display name (family + variant when unnamed).
"""

from __future__ import annotations

from sqlalchemy import Connection, text

from agentkit.tools.get_product import product_display_name


def list_products(conn: Connection, division: str, category: str | None = None) -> dict:
    """Return ``{"division", "families": [{family_id, name, category, products: [...]}]}``."""
    params: dict[str, object] = {"division": division}
    category_clause = ""
    if category is not None:
        category_clause = " AND pf.category ILIKE :category"
        params["category"] = category
    families = conn.execute(
        text(
            "SELECT pf.family_id, pf.name, pf.category, pf.division, pf.summary "
            "FROM facts.active_product_family pf "
            f"WHERE pf.division = :division{category_clause} ORDER BY pf.family_id"
        ),
        params,
    ).mappings().all()

    products = conn.execute(
        text(
            "SELECT product_id, family_id, model_name, variant, description "
            "FROM facts.active_product ORDER BY product_id"
        )
    ).mappings().all()
    by_family: dict[str, list[dict]] = {}
    for p in products:
        by_family.setdefault(p["family_id"], []).append(p)

    out_families = []
    for fam in families:
        prods = [
            {
                "product_id": p["product_id"],
                "display_name": product_display_name(
                    p["model_name"], fam["name"], p["variant"], p["description"]
                ),
                "model_name": p["model_name"],
                "variant": p["variant"],
            }
            for p in by_family.get(fam["family_id"], [])
        ]
        out_families.append(
            {
                "family_id": fam["family_id"],
                "name": fam["name"],
                "category": fam["category"],
                "summary": fam["summary"],
                "products": prods,
            }
        )
    return {"division": division, "families": out_families}
