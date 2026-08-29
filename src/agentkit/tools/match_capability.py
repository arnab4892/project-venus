"""``match_capability`` — LLD-TOOL-01 (revised, milestone-5a live-run fix).

A pure function over the ``facts.active_*`` views. Given a gas duty it returns:

* ``matches`` — rows where **every requested comparable filter passed**, ranked by fit; each
  match carries the row's own published limits, its own per-dimension ``headroom`` and its own
  ``near_edge`` flag. A row that FAILS a comparable filter (e.g. a 700 Nm³/hr ceiling for a
  3000 Nm³/hr duty) is **excluded entirely** — it is neither a match nor a candidate.
* ``non_comparable_candidates`` — rows the filter **could not evaluate** (unit mismatch, e.g.
  a Kg/hr capacity against an Nm³/hr query, or an unpublished/null limit), each with a
  ``reason``. These are never presented as matches; the runtime may offer an engineer review.
* ``any_near_edge`` — OR of the matches' own ``near_edge`` flags.

This replaces the earlier single global ``near_edge`` + the behaviour where a non-comparable
row leaked into ``matches`` with ``headroom None`` (the live hydrogen turn's fuelling-page
mismatch, ops turn ab357000). Reads only the active-release views (LLD-DB-02), never filters
``release_id``. Unit handling (Nm³/hr ↔ SCMD, barg) is delegated to
:mod:`agentkit.extract.normalise` (LLD-EXT-09). Per-client **gas alias map**
(``hydrogen`` → ``H2`` …) is applied at query time; facts stay verbatim. A null ``lubricated``
means "unspecified / both offered" — kept under an oil-free filter, flagged.

**LLD note (rule 6, for the doc pass):** LLD-TOOL-01's result shape gains
``non_comparable_candidates`` + per-match ``near_edge`` and drops the single global flag; fold
into LLD-TOOL-01 via the clarification path.
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
SELECT cr.cap_id, cr.family_id, pf.name AS family_name, cr.lubricated,
       cr.capacity_min, cr.capacity_max, cr.capacity_unit,
       cr.discharge_p_min, cr.discharge_p_max, cr.pressure_unit,
       cr.standards
FROM facts.active_capability_row cr
JOIN facts.active_product_family pf ON pf.family_id = cr.family_id
JOIN facts.active_capability_gas cg ON cg.cap_id = cr.cap_id
WHERE cg.gas = ANY(:gas_terms)
{lubricated_clause}
{standard_clause}
ORDER BY cr.cap_id
"""


def _canon_gas(term: str, aliases: dict[str, str]) -> str:
    """Canonicalise a gas name for comparison: alias map first, else case-folded."""
    key = term.strip().lower()
    return aliases.get(key, key)


def _gas_terms(conn: Connection, gas: str, aliases: dict[str, str]) -> list[str]:
    """Stored gas strings in the active release equivalent to the query gas."""
    canon_q = _canon_gas(gas, aliases)
    stored = conn.execute(
        text("SELECT DISTINCT gas FROM facts.active_capability_gas")
    ).scalars().all()
    return [g for g in stored if _canon_gas(g, aliases) == canon_q]


def _as_float(value) -> float | None:
    return None if value is None else float(value)


def _safe_convert_capacity(capacity: float, query_unit: str, row_unit) -> float | None:
    """Convert the query capacity into a row's unit, or ``None`` if not comparable."""
    if row_unit is None:
        return None
    try:
        return convert_capacity(capacity, query_unit, row_unit)
    except ValueError:
        return None


def _pressure_comparable(row_unit) -> bool:
    """True iff the row's pressure unit canonicalises to the query basis (barg)."""
    if row_unit is None:
        return False
    try:
        return canonical_pressure_unit(row_unit) == canonical_pressure_unit("barg")
    except ValueError:
        return False


def _within(value: float, low, high) -> bool:
    """value ∈ [low, high], treating a null bound as open on that side."""
    if low is not None and value < low:
        return False
    if high is not None and value > high:
        return False
    return True


def _headroom(value: float, limit) -> float | None:
    """1 − value/limit, or ``None`` when there is no published upper limit."""
    return None if not limit else 1 - value / limit


def _fit_key(match: dict) -> tuple:
    """Rank matches by fit: not-near-edge first, then more margin (avg headroom) first."""
    hs = [h for h in match["headroom"].values() if h is not None]
    avg = sum(hs) / len(hs) if hs else 0.0
    return (match["near_edge"], -avg, match["cap_id"])


def match_capability(
    conn: Connection,
    gas: str,
    capacity: float,
    capacity_unit: str,
    discharge_p: float,
    lubricated: bool | None = None,
    standard: str | None = None,
    gas_aliases: dict[str, str] | None = None,
) -> dict:
    """Return ``{"matches": [...], "non_comparable_candidates": [...], "any_near_edge": bool}``.

    A ``match`` carries the row's published limits (``capacity_min/max`` + unit,
    ``discharge_p_min/max`` + unit, ``standards``, ``family_name``), its ``headroom`` per
    dimension (``1 − value/limit``, ``None`` where no upper limit is published) and its own
    ``near_edge`` (a value within 10% of a published limit). Rows whose unit is non-comparable
    on a requested dimension go to ``non_comparable_candidates`` with a ``reason`` instead —
    never guessed, never presented as a match. Rows that fail a comparable filter are excluded.
    """
    aliases = {str(k).strip().lower(): str(v) for k, v in (gas_aliases or {}).items()}
    canonical_pressure_unit("barg")  # validate the fixed pressure basis
    query_cap_unit = canonical_capacity_unit(capacity_unit)

    params: dict[str, object] = {"gas_terms": _gas_terms(conn, gas, aliases)}
    lubricated_clause = ""
    if lubricated is not None:
        lubricated_clause = "AND (cr.lubricated = :lubricated OR cr.lubricated IS NULL)"
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
    non_comparable: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if row["cap_id"] in seen:  # a cap serving several equivalent gas forms
            continue
        seen.add(row["cap_id"])

        cap_max = _as_float(row["capacity_max"])
        cap_min = _as_float(row["capacity_min"])
        p_max = _as_float(row["discharge_p_max"])
        p_min = _as_float(row["discharge_p_min"])

        conv_capacity = _safe_convert_capacity(capacity, query_cap_unit, row["capacity_unit"])
        cap_comparable = conv_capacity is not None
        p_comparable = _pressure_comparable(row["pressure_unit"])

        reasons: list[str] = []
        failed = False

        # capacity dimension
        if cap_comparable:
            if not _within(conv_capacity, cap_min, cap_max):
                failed = True
        elif row["capacity_unit"]:
            reasons.append(
                f"capacity unit {row['capacity_unit']!r} not comparable to {query_cap_unit!r}"
            )
        else:
            reasons.append("capacity not published")

        # pressure dimension
        if p_comparable:
            if not _within(discharge_p, p_min, p_max):
                failed = True
        elif row["pressure_unit"]:
            reasons.append(f"pressure unit {row['pressure_unit']!r} not comparable to 'barg'")
        else:
            reasons.append("pressure not published")

        base = {
            "cap_id": row["cap_id"],
            "family_id": row["family_id"],
            "family_name": row["family_name"],
        }

        if failed:
            # a comparable filter failed → the duty is outside this row's published envelope.
            continue
        if reasons:
            # a requested dimension could not be evaluated → candidate, never a match.
            non_comparable.append({**base, "reason": "; ".join(reasons)})
            continue

        # both requested dimensions comparable and within envelope → a real match.
        cap_ratio = conv_capacity / cap_max if cap_max else None
        p_ratio = discharge_p / p_max if p_max else None
        near_edge = (cap_ratio is not None and cap_ratio > _NEAR_EDGE_RATIO) or (
            p_ratio is not None and p_ratio > _NEAR_EDGE_RATIO
        )
        match = {
            **base,
            "capacity_min": cap_min,
            "capacity_max": cap_max,
            "capacity_unit": row["capacity_unit"],
            "discharge_p_min": p_min,
            "discharge_p_max": p_max,
            "pressure_unit": row["pressure_unit"],
            "standards": list(row["standards"] or []),
            "lubricated": row["lubricated"],
            "headroom": {
                "capacity": _headroom(conv_capacity, cap_max),
                "pressure": _headroom(discharge_p, p_max),
            },
            "near_edge": near_edge,
        }
        if row["lubricated"] is None:
            match["lubricated_unspecified"] = True
        matches.append(match)

    matches.sort(key=_fit_key)
    return {
        "matches": matches,
        "non_comparable_candidates": non_comparable,
        "any_near_edge": any(m["near_edge"] for m in matches),
    }
