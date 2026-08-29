"""``get_company_fact`` — LLD-TOOL-05.

Return the company facts of a given kind (``certification``, ``founded``, ``coverage``,
``client``, ``facility``, ``founder``, ``industry_served`` …) with their provenance.
"""

from __future__ import annotations

from sqlalchemy import Connection, text


def get_company_fact(conn: Connection, kind: str) -> dict:
    """Return ``{"kind", "facts": [{fact_id, value, detail, source_doc_id, source_locator}]}``."""
    rows = conn.execute(
        text(
            "SELECT fact_id, kind, value, detail, source_doc_id, source_locator "
            "FROM facts.active_company_fact WHERE kind = :kind ORDER BY fact_id"
        ),
        {"kind": kind},
    ).mappings().all()
    return {"kind": kind, "facts": [dict(r) for r in rows]}
