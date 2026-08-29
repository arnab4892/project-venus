"""list_products / get_product / get_company_fact / get_office (LLD-TOOL-02/03/05/06).

DB cases run on the seeded demo release; a couple of pure cases cover the display-name rule.
"""

from __future__ import annotations

from sqlalchemy import text

from agentkit.tools.get_company_fact import get_company_fact
from agentkit.tools.get_office import get_office
from agentkit.tools.get_product import get_product, normalise_alias, product_display_name
from agentkit.tools.list_products import list_products


# --- get_product ------------------------------------------------------------


def test_alias_normalisation_strips_spaces_and_hyphens():
    assert normalise_alias("MCH-16") == normalise_alias("MCH 16") == "mch16"


def test_get_product_matches_model_by_alias(seeded_conn):
    result = get_product(seeded_conn, "mch6")  # stored as 'MCH-6'
    assert result["matched_by"] == "model"
    assert [p["product_id"] for p in result["products"]] == ["prd.mch6"]
    assert result["products"][0]["display_name"] == "MCH-6"


def test_get_product_by_family_returns_all(seeded_conn):
    result = get_product(seeded_conn, "fam.mch_bac")
    assert result["matched_by"] == "family"
    assert len(result["products"]) == 4


def test_null_model_presented_by_family_name():
    # LLD-EXT-06: a name-less product is shown by family + variant/description, never blank.
    assert product_display_name(None, "Diaphragm Compressor", "300 bar", None) == (
        "Diaphragm Compressor — 300 bar"
    )
    assert product_display_name(None, "Air Lifting Bags", None, None) == "Air Lifting Bags"
    assert product_display_name("MCH-6", "MCH Series", None, None) == "MCH-6"


def test_get_product_null_model_from_db(seeded_conn):
    conn = seeded_conn
    conn.execute(text(
        "INSERT INTO facts.product (product_id, release_id, family_id, model_name, variant, "
        " description, attributes, source_doc_id, source_locator) "
        "SELECT 'prd.noname', 'r2026.08.1', 'fam.diaphragm', NULL, 'high-pressure', NULL, "
        " '{}'::jsonb, source_doc_id, source_locator "
        "FROM facts.product WHERE release_id='r2026.08.1' AND product_id='prd.mch6'"
    ))
    result = get_product(conn, "fam.diaphragm")
    names = [p["display_name"] for p in result["products"]]
    assert "Diaphragm Compressor — high-pressure" in names


# --- list_products ----------------------------------------------------------


def test_list_products_by_division(seeded_conn):
    result = list_products(seeded_conn, "diving")
    fam_ids = {f["family_id"] for f in result["families"]}
    assert fam_ids == {"fam.diving_masks"}
    prod_names = [p["display_name"] for f in result["families"] for p in f["products"]]
    assert "NEPTUNE III" in prod_names


# --- get_company_fact -------------------------------------------------------


def test_get_company_fact_returns_rows_and_empty(seeded_conn):
    assert get_company_fact(seeded_conn, "founded")["facts"]  # non-empty
    assert get_company_fact(seeded_conn, "does_not_exist")["facts"] == []


# --- get_office -------------------------------------------------------------


def test_get_office_by_region(seeded_conn):
    result = get_office(seeded_conn, region="South")
    assert result["fallback"] is False
    assert result["matched_by"] == "region"
    assert result["office"]["city"] == "Chennai"


def test_get_office_head_office_fallback(seeded_conn):
    result = get_office(seeded_conn, city="Atlantis")  # no such office
    assert result["fallback"] is True
    assert result["office"]["name"] == "Head Office"
