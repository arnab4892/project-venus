"""``get_office`` — LLD-TOOL-06.

Resolve an office by city, state or region, falling back to the head office when nothing
matches. Offices carry no ``region`` value directly; the region/state → office mapping lives
in ``facts.active_region_state`` (the curated per-client seed), so state/region lookups join
through it.
"""

from __future__ import annotations

from sqlalchemy import Connection, text

_COLS = (
    "o.office_id, o.name, o.city, o.region, o.address, o.phone, o.email, o.serves_divisions"
)


def _head_office(conn: Connection) -> dict | None:
    """The head office: a 'Head-Quarters'-named office (else the NOIDA corporate office)."""
    row = conn.execute(
        text(
            f"SELECT {_COLS} FROM facts.active_office o "
            "WHERE o.name ILIKE '%head%' OR o.city ILIKE 'NOIDA' "
            "ORDER BY o.office_id LIMIT 1"
        )
    ).mappings().first()
    return dict(row) if row else None


def get_office(
    conn: Connection,
    *,
    city: str | None = None,
    state: str | None = None,
    region: str | None = None,
) -> dict:
    """Return ``{"office", "matched_by", "fallback"}`` for a city/state/region lookup.

    A city lookup hits ``facts.active_office`` directly; a state or region lookup joins
    ``facts.active_region_state``. When nothing matches, the head office is returned with
    ``fallback: True`` (LLD-TOOL-06).
    """
    row = None
    matched_by = None
    if city is not None:
        row = conn.execute(
            text(
                f"SELECT {_COLS} FROM facts.active_office o "
                "WHERE o.city ILIKE :v ORDER BY o.office_id LIMIT 1"
            ),
            {"v": city},
        ).mappings().first()
        matched_by = "city"
    elif state is not None or region is not None:
        column = "rs.state" if state is not None else "rs.region"
        row = conn.execute(
            text(
                f"SELECT {_COLS} FROM facts.active_office o "
                "JOIN facts.active_region_state rs ON rs.office_id = o.office_id "
                f"WHERE {column} ILIKE :v ORDER BY o.office_id LIMIT 1"
            ),
            {"v": state if state is not None else region},
        ).mappings().first()
        matched_by = "state" if state is not None else "region"

    if row is not None:
        return {"office": dict(row), "matched_by": matched_by, "fallback": False}
    return {"office": _head_office(conn), "matched_by": None, "fallback": True}
