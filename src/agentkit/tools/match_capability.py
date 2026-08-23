"""``match_capability`` — LLD-TOOL-01.

A pure function over the ``facts.active_*`` views: given a gas duty, return the
capability rows whose published envelope contains it, each with its remaining
headroom, plus a ``near_edge`` flag when any published limit is within 10%.

Reads only the active-release views (LLD-DB-02) and never filters ``release_id``.
Unit handling (Nm³/hr ↔ SCMD, barg) is delegated to
:mod:`agentkit.extract.normalise` (LLD-EXT-09).
"""

from __future__ import annotations

from sqlalchemy import Connection, text

from agentkit.extract.normalise import (
    canonical_capacity_unit,
    canonical_pressure_unit,
    convert_capacity,
)

# near_edge trips when a query value reaches this fraction of a published limit.
_NEAR_EDGE_RATIO = 0.9

_CANDIDATE_SQL = """
SELECT cr.cap_id, cr.family_id, cr.lubricated,
       cr.capacity_min, cr.capacity_max, cr.capacity_unit,
       cr.discharge_p_min, cr.discharge_p_max, cr.pressure_unit
FROM facts.active_capability_row cr
JOIN facts.active_product_family pf ON pf.family_id = cr.family_id
JOIN facts.active_capability_gas cg ON cg.cap_id = cr.cap_id
WHERE lower(cg.gas) = lower(:gas)
{lubricated_clause}
{standard_clause}
ORDER BY cr.cap_id
"""


def _as_float(value) -> float | None:
    return None if value is None else float(value)


def match_capability(
    conn: Connection,
    gas: str,
    capacity: float,
    capacity_unit: str,
    discharge_p: float,
    lubricated: bool | None = None,
    standard: str | None = None,
) -> dict:
    """Return ``{"matches": [...], "near_edge": bool}`` for a gas/flow/pressure duty.

    Each match is ``{"cap_id", "family_id", "headroom": {"capacity", "pressure"}}``
    where headroom is ``1 − value/limit`` per dimension (``None`` when the limit is
    unpublished). ``near_edge`` is true when any matched cap has a value within 10%
    of a published limit. Pressure is treated as ``barg``; capacity is converted to
    each cap's stored unit before comparison.
    """
    canonical_pressure_unit("barg")  # validate the fixed pressure basis
    query_cap_unit = canonical_capacity_unit(capacity_unit)

    params: dict[str, object] = {"gas": gas}
    lubricated_clause = ""
    if lubricated is not None:
        lubricated_clause = "AND cr.lubricated = :lubricated"
        params["lubricated"] = lubricated
    standard_clause = ""
    if standard is not None:
        standard_clause = "AND :standard = ANY(cr.standards)"
        params["standard"] = standard

    sql = _CANDIDATE_SQL.format(
        lubricated_clause=lubricated_clause, standard_clause=standard_clause
    )
    rows = conn.execute(text(sql), params).mappings().all()

    matches: list[dict] = []
    near_edge = False
    for row in rows:
        cap_max = _as_float(row["capacity_max"])
        cap_min = _as_float(row["capacity_min"])
        p_max = _as_float(row["discharge_p_max"])
        p_min = _as_float(row["discharge_p_min"])

        # normalise the query capacity into this cap's stored unit
        conv_capacity = convert_capacity(capacity, query_cap_unit, row["capacity_unit"])

        # capacity envelope
        if cap_max is not None and conv_capacity > cap_max:
            continue
        if cap_min is not None and conv_capacity < cap_min:
            continue
        # pressure envelope (barg)
        if p_max is not None and discharge_p > p_max:
            continue
        if p_min is not None and discharge_p < p_min:
            continue

        cap_ratio = conv_capacity / cap_max if cap_max else None
        p_ratio = discharge_p / p_max if p_max else None
        if (cap_ratio is not None and cap_ratio > _NEAR_EDGE_RATIO) or (
            p_ratio is not None and p_ratio > _NEAR_EDGE_RATIO
        ):
            near_edge = True

        matches.append(
            {
                "cap_id": row["cap_id"],
                "family_id": row["family_id"],
                "headroom": {
                    "capacity": None if cap_ratio is None else 1 - cap_ratio,
                    "pressure": None if p_ratio is None else 1 - p_ratio,
                },
            }
        )

    return {"matches": matches, "near_edge": near_edge}
