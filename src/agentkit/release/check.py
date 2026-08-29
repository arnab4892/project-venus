"""Release integrity check on the approved+edited set of an RC (LLD-REL-03).

Hard failures block promote; warnings are listed but do not. Runs read-only on the
caller's ``Connection`` over ``staging.*`` (the surviving = ``approved``/``edited``
rows only) plus the frozen ``families.yaml`` and the curated ``region_state`` seed.

Hard failures
  * reference closure — ``product``/``capability_row`` ``family_id`` in
    ``families.yaml``; each referenced family's parsed ``source_doc_id`` is a
    surviving document; every surviving ``capability_gas`` has a surviving parent;
  * provenance present — ``product``/``capability_row``/``company_fact``/``office``
    carry ``source_doc_id`` + ``source_locator`` (``capability_gas`` inherits its
    parent's, ``document`` is the source, ``region_state`` is exempt per LLD-DB-07);
  * a capacity/discharge numeric with no unit (listed with its evidence quote);
  * duplicate natural ids within a sheet; ``needs_family`` still true on a survivor;
  * an office whose city does not resolve via ``region_state``.

Warnings
  * a ``families.yaml`` family with no surviving capability row or product;
  * surviving rows still sharing a ``conflict_group`` (reviewer kept both — allowed).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Connection, text

from agentkit.extract.families import load_families, load_family_ids
from agentkit.release.curated import city_to_state, parse_source

_SOURCE_TABLES = ("product", "capability_row", "company_fact", "office")
_NATURAL_ID = {
    "document": "doc_id", "product": "product_id", "capability_row": "cap_id",
    "company_fact": "fact_id", "office": "office_id",
}


@dataclass
class CheckReport:
    rc_id: str
    hard_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    region_mapping: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.hard_failures


def _surviving(conn: Connection, rc: str, table: str, cols: str = "*") -> list:
    return conn.execute(
        text(f"SELECT {cols} FROM staging.{table} WHERE release_candidate_id = :rc "
             "AND review_status IN ('approved', 'edited')"),
        {"rc": rc},
    ).mappings().all()


def _facts_nn_columns(conn: Connection, table: str) -> list[str]:
    """The NOT-NULL columns of ``facts.<table>`` (excluding ``release_id``)."""
    return [
        r[0] for r in conn.execute(
            text("SELECT column_name FROM information_schema.columns "
                 "WHERE table_schema='facts' AND table_name=:t AND is_nullable='NO' "
                 "AND column_name <> 'release_id'"),
            {"t": table},
        ).all()
    ]


def check_rc(conn: Connection, rc_id: str, *, client: str) -> CheckReport:
    r = CheckReport(rc_id)
    family_ids = load_family_ids(client)
    families = {f["id"]: f for f in load_families(client)}

    docs = {d["doc_id"] for d in _surviving(conn, rc_id, "document", "doc_id")}
    products = _surviving(conn, rc_id, "product")
    caps = _surviving(conn, rc_id, "capability_row")
    gases = _surviving(conn, rc_id, "capability_gas")
    offices = _surviving(conn, rc_id, "office")

    surviving_caps = {c["cap_id"] for c in caps}
    referenced_families: set[str] = set()

    # --- duplicate natural ids + needs_family + provenance per source table ------
    for table in ("document", *_SOURCE_TABLES):
        rows = _surviving(conn, rc_id, table)
        nid = _NATURAL_ID[table]
        # Generic safety net: every facts NOT-NULL column present in staging must be
        # non-null on survivors, so promote never dies on a raw DB error mid-insert.
        nn_cols = [c for c in _facts_nn_columns(conn, table)
                   if rows and c in rows[0].keys()]
        seen: set = set()
        for row in rows:
            key = row[nid]
            if key in seen:
                r.hard_failures.append(f"{table}: duplicate natural id {key!r}")
            seen.add(key)
            for col in nn_cols:
                if row.get(col) is None:
                    r.hard_failures.append(
                        f"{table} {key}: required column {col!r} is null (facts.{table} NOT NULL)")
            if row.get("needs_family"):
                r.hard_failures.append(f"{table} {key}: needs_family still true on a survivor")
            if table in _SOURCE_TABLES:
                src = row.get("source_doc_id")
                if not src or not row.get("source_locator"):
                    r.hard_failures.append(f"{table} {key}: missing source_doc_id/source_locator")
                elif src not in docs:
                    r.hard_failures.append(
                        f"{table} {key}: source_doc_id {src!r} is not a surviving document")

    # --- reference closure: product / capability_row family_id -------------------
    for row in products:
        fid = row.get("family_id")
        if fid:
            referenced_families.add(fid)
            if fid not in family_ids:
                r.hard_failures.append(f"product {row['product_id']}: family_id {fid!r} not in families.yaml")
    for row in caps:
        fid = row.get("family_id")
        if fid:
            referenced_families.add(fid)
            if fid not in family_ids:
                r.hard_failures.append(f"capability_row {row['cap_id']}: family_id {fid!r} not in families.yaml")

    # each referenced family's source doc must be a surviving document
    for fid in sorted(referenced_families):
        fam = families.get(fid)
        if not fam:
            continue  # already reported as not-in-yaml above
        src_doc, _ = parse_source(fam.get("source"))
        if src_doc and src_doc not in docs:
            r.hard_failures.append(
                f"product_family {fid}: source document {src_doc!r} is not a surviving document")

    # --- capability_gas relational parent ---------------------------------------
    for g in gases:
        if g["cap_id"] not in surviving_caps:
            r.hard_failures.append(
                f"capability_gas ({g['cap_id']}, {g['gas']}): parent capability_row not in surviving set")

    # --- numeric without a unit (hard; list with evidence quote) ----------------
    for c in caps:
        ev = c.get("evidence") or {}
        if (c.get("capacity_min") is not None or c.get("capacity_max") is not None) and not c.get("capacity_unit"):
            r.hard_failures.append(
                f"capability_row {c['cap_id']}: capacity numeric without a unit "
                f"(evidence: {ev.get('capacity')!r})")
        if (c.get("discharge_p_min") is not None or c.get("discharge_p_max") is not None) and not c.get("pressure_unit"):
            r.hard_failures.append(
                f"capability_row {c['cap_id']}: discharge numeric without a unit "
                f"(evidence: {ev.get('discharge_pressure')!r})")

    # --- warnings: orphan families + surviving conflict groups -------------------
    for fid in sorted(family_ids):
        if fid not in referenced_families:
            r.warnings.append(f"family {fid} has no surviving capability row or product")
    groups: dict[str, int] = {}
    for c in caps:
        g = c.get("conflict_group")
        if g:
            groups[g] = groups.get(g, 0) + 1
    for g, n in sorted(groups.items()):
        if n >= 2:
            r.warnings.append(f"conflict_group {g}: {n} surviving rows kept (reviewer adjudicated)")

    # --- offices resolve via region_state ---------------------------------------
    lookup = city_to_state(client)
    for o in sorted(offices, key=lambda x: x["office_id"]):
        city = (o.get("city") or "").strip()
        meta = lookup.get(city.upper())
        if meta:
            r.region_mapping.append(
                f"{o['office_id']:36} {city:12} → {meta['state']:16} → {meta['region']}")
        else:
            r.hard_failures.append(
                f"office {o['office_id']}: city {city!r} does not resolve via region_state")

    return r


def format_report(r: CheckReport) -> str:
    lines = [f"release check {r.rc_id}: {'PASS' if r.ok else 'FAIL'}"]
    if r.hard_failures:
        lines.append(f"\nHARD FAILURES ({len(r.hard_failures)}):")
        lines += [f"  ✗ {m}" for m in r.hard_failures]
    if r.warnings:
        lines.append(f"\nwarnings ({len(r.warnings)}):")
        lines += [f"  ! {m}" for m in r.warnings]
    if r.region_mapping:
        lines.append(f"\noffice → region_state mapping ({len(r.region_mapping)}):")
        lines += [f"  {m}" for m in r.region_mapping]
    return "\n".join(lines)
