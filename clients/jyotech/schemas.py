"""Jyotech extraction schemas + row mapping (LLD-EXT-04…08).

Client-specific, by design: the framework (``src/agentkit/extract``) owns the
machinery — section split, the verbatim evidence gate, unit/range normalisation,
the LLM call, staging upserts — and stays free of Jyotech strings. This module
supplies what is Jyotech-shaped: the typed Pydantic schemas (each field an
``Evidenced`` ``{value, evidence}``), which classifier type maps to which schema,
and how a gated extraction becomes ``staging.*`` rows.

Each schema is a *container* holding a list, because one section can describe
several products / offices / facts. Numeric and unit parsing is done here in
Python via :mod:`agentkit.extract.normalise` **after** extraction — never asked
of the model — per LLD-EXT-09.
"""

from __future__ import annotations

import re
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from agentkit.extract.evidence import unwrap
from agentkit.extract.normalise import parse_capacity, parse_pressure

T = TypeVar("T")

COMPANY_FACT_KINDS = (
    "certification", "founded", "founder", "facility",
    "industry_served", "client", "coverage", "contact",
)


class Evidenced(BaseModel, Generic[T]):
    """A single extracted value with the verbatim phrase that supports it."""

    value: T
    evidence: str


# --------------------------------------------------------------------------- #
# Typed schemas — one container per classifier type.
# --------------------------------------------------------------------------- #

class CapabilityRowOut(BaseModel):
    family: Evidenced[str] | None = None           # value = a frozen family id
    comp_type: Evidenced[str] | None = None
    lubricated: Evidenced[bool] | None = None
    cooling: Evidenced[str] | None = None
    capacity: Evidenced[str] | None = None         # printed phrase, e.g. "up to 20000 Nm3/hr"
    discharge_pressure: Evidenced[str] | None = None
    drivers: list[Evidenced[str]] = []
    standards: list[Evidenced[str]] = []
    gases: list[Evidenced[str]] = []


class ProductOut(BaseModel):
    family: Evidenced[str] | None = None
    model_name: Evidenced[str] | None = None
    variant: Evidenced[str] | None = None
    description: Evidenced[str] | None = None
    attributes: list[Evidenced[str]] = []          # only what is printed


class CompanyFactOut(BaseModel):
    kind: Evidenced[str] | None = None             # one of COMPANY_FACT_KINDS
    value: Evidenced[str] | None = None
    detail: Evidenced[str] | None = None


class OfficeOut(BaseModel):
    name: Evidenced[str] | None = None
    city: Evidenced[str] | None = None
    address: Evidenced[str] | None = None
    phone: Evidenced[str] | None = None
    email: Evidenced[str] | None = None
    serves_divisions: list[Evidenced[str]] = []


class CapabilityExtraction(BaseModel):
    rows: list[CapabilityRowOut] = []


class ProductExtraction(BaseModel):
    products: list[ProductOut] = []


class CompanyFactExtraction(BaseModel):
    facts: list[CompanyFactOut] = []


class OfficeExtraction(BaseModel):
    offices: list[OfficeOut] = []


class FamilyProposal(BaseModel):
    """Pass-1 family-discovery output (LLD-EXT-03)."""

    id: str
    division: str
    category: str
    name: str
    summary: str
    source: str


class FamilyDiscovery(BaseModel):
    families: list[FamilyProposal] = []


# classifier type -> extraction container schema
SCHEMAS: dict[str, type[BaseModel]] = {
    "capability_spec": CapabilityExtraction,
    "product_list": ProductExtraction,
    "company_fact": CompanyFactExtraction,
    "office_contact": OfficeExtraction,
}

# classifier type -> the list key inside its container
_LIST_KEY = {
    "capability_spec": "rows",
    "product_list": "products",
    "company_fact": "facts",
    "office_contact": "offices",
}


# Parametrised generics (``Evidenced[str]``) need an explicit rebuild before the
# containers can emit a JSON schema.
for _m in (
    CapabilityRowOut, ProductOut, CompanyFactOut, OfficeOut,
    CapabilityExtraction, ProductExtraction, CompanyFactExtraction, OfficeExtraction,
    FamilyDiscovery,
):
    _m.model_rebuild()


def json_schema_for(type_: str) -> dict[str, Any]:
    """The JSON schema the extractor LLM must satisfy for a classifier ``type_``."""
    return SCHEMAS[type_].model_json_schema()


def family_discovery_schema() -> dict[str, Any]:
    return FamilyDiscovery.model_json_schema()


# --------------------------------------------------------------------------- #
# Gated extraction dict -> staging rows.
# --------------------------------------------------------------------------- #

def _slug(section_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", section_id.lower()).strip("_")


def _row_id(prefix: str, section_id: str, i: int) -> str:
    return f"{prefix}.{_slug(section_id)}.{i}"


def _ev_phrase(node: Any) -> str | None:
    if isinstance(node, dict) and "evidence" in node:
        return node.get("evidence")
    return None


def _evidence_map(item: dict) -> dict[str, Any]:
    """Collect ``{field: evidence phrase}`` for every present Evidenced node."""
    out: dict[str, Any] = {}
    for key, node in item.items():
        if isinstance(node, dict) and "evidence" in node:
            out[key] = node.get("evidence")
        elif isinstance(node, list):
            phrases = [_ev_phrase(x) for x in node if _ev_phrase(x)]
            if phrases:
                out[key] = phrases
    return out


def _list_values(node: Any) -> list[str]:
    return [unwrap(x) for x in (node or []) if unwrap(x) is not None]


def _family_id(item: dict, family_ids: set[str]) -> tuple[str | None, bool]:
    """Resolve a frozen family id. Unknown/absent → ``(None, needs_family=True)``.

    Frozen-family rule (LLD-EXT-03): an extractor may only assign an id already
    in ``families.yaml``. Anything else is flagged, never invented.
    """
    fid = unwrap(item.get("family"))
    if fid and fid in family_ids:
        return fid, False
    return None, True


def build_rows(
    section: Any,
    type_: str,
    gated: dict,
    *,
    family_ids: set[str],
    confidence: float | None,
) -> list[tuple[str, dict]]:
    """Turn one gated section extraction into ``(table, row)`` staging tuples."""
    items = gated.get(_LIST_KEY[type_], []) or []
    prov = dict(
        source_doc_id=section.doc_id,
        source_locator=section.locator,
        section_id=section.section_id,
        confidence=confidence,
        review_status="pending",
    )
    out: list[tuple[str, dict]] = []

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        evidence = _evidence_map(item)

        if type_ == "capability_spec":
            fid, needs_family = _family_id(item, family_ids)
            cap = parse_capacity(unwrap(item.get("capacity")) or "") if item.get("capacity") else None
            dp = (
                parse_pressure(unwrap(item.get("discharge_pressure")) or "")
                if item.get("discharge_pressure")
                else None
            )
            cap_id = _row_id("cap", section.section_id, i)
            row = dict(
                cap_id=cap_id,
                family_id=fid,
                comp_type=unwrap(item.get("comp_type")),
                lubricated=unwrap(item.get("lubricated")),
                cooling=unwrap(item.get("cooling")),
                capacity_min=cap.min if cap else None,
                capacity_max=cap.max if cap else None,
                capacity_unit=cap.unit if cap else None,
                discharge_p_min=dp.min if dp else None,
                discharge_p_max=dp.max if dp else None,
                pressure_unit=dp.unit if dp else None,
                driver=_list_values(item.get("drivers")),
                standards=_list_values(item.get("standards")),
                needs_family=needs_family,
                conflict_group=None,
                evidence=evidence,
                **prov,
            )
            out.append(("capability_row", row))
            # capability_gas mirrors only (cap_id, gas) + review/marker columns —
            # it has no source_doc_id/source_locator of its own.
            for gas in _list_values(item.get("gases")):
                out.append((
                    "capability_gas",
                    dict(cap_id=cap_id, gas=gas, needs_family=False, conflict_group=None,
                         evidence={"gas": gas}, section_id=section.section_id,
                         confidence=confidence, review_status="pending"),
                ))

        elif type_ == "product_list":
            fid, needs_family = _family_id(item, family_ids)
            out.append(("product", dict(
                product_id=_row_id("prd", section.section_id, i),
                family_id=fid,
                model_name=unwrap(item.get("model_name")),
                variant=unwrap(item.get("variant")),
                description=unwrap(item.get("description")),
                attributes={"printed": _list_values(item.get("attributes"))},
                needs_family=needs_family,
                conflict_group=None,
                evidence=evidence,
                **prov,
            )))

        elif type_ == "company_fact":
            kind = unwrap(item.get("kind"))
            out.append(("company_fact", dict(
                fact_id=_row_id("cf", section.section_id, i),
                kind=kind if kind in COMPANY_FACT_KINDS else None,
                value=unwrap(item.get("value")),
                detail={"detail": unwrap(item.get("detail"))} if item.get("detail") else {},
                needs_family=False,
                conflict_group=None,
                evidence=evidence,
                **prov,
            )))

        elif type_ == "office_contact":
            out.append(("office", dict(
                office_id=_row_id("off", section.section_id, i),
                name=unwrap(item.get("name")),
                city=unwrap(item.get("city")),
                region=None,  # resolved from region_state at the release gate
                address=unwrap(item.get("address")),
                phone=unwrap(item.get("phone")),
                email=unwrap(item.get("email")),
                serves_divisions=_list_values(item.get("serves_divisions")),
                needs_family=False,
                conflict_group=None,
                evidence=evidence,
                **prov,
            )))

    return out


def mark_conflicts(cap_rows: list[dict]) -> None:
    """Link unresolved same-family capacity conflicts (LLD-EXT: 20000 vs 25000).

    Two capability rows for the same family with different published
    ``capacity_max`` are a genuine source conflict (the web page says 20000, the
    PDF says 25000 for PROCESS COMPRESSORS RECIP.). We **extract both** and set a
    shared ``conflict_group`` so the reviewer adjudicates — we never pick one.
    Mutates the rows in place.
    """
    by_family: dict[str, list[dict]] = {}
    for row in cap_rows:
        fid = row.get("family_id")
        if fid:
            by_family.setdefault(fid, []).append(row)
    for fid, rows in by_family.items():
        maxes = {r.get("capacity_max") for r in rows if r.get("capacity_max") is not None}
        if len(maxes) >= 2:
            group = f"cg.{fid}.capacity"
            for r in rows:
                if r.get("capacity_max") is not None:
                    r["conflict_group"] = group
