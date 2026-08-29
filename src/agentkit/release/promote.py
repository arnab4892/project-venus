"""Promote / activate / rollback (LLD-REL-05).

``promote`` inserts the approved+edited staging rows into ``facts.*`` under a new
``rYYYY.MM.N`` release (not active yet), builds ``facts.product_family`` from the
frozen ``families.yaml`` and ``facts.region_state`` from the curated seed, and
asserts same-release FK closure. Embedding (LLD-RET) is triggered by the ``release
promote`` CLI after this returns — best-effort in its own transaction so an
unreachable embedder never rolls the promote back (run ``release embed`` separately).

The **promote gate** (superseding the deliberately absent staging FK): refuse an
RC with no ``staging.release_candidate`` ledger row, or whose status is not
``exported``/``imported`` — so the bootstrap RC is never promotable.

``activate`` flips the single active release (partial unique index enforces one);
``rollback`` is the same flip aimed at a prior release. All run on the caller's
``Connection`` and never commit.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Connection, text

from agentkit.extract.families import load_families
from agentkit.release.candidate import get_rc, set_status
from agentkit.release.curated import load_region_states, parse_source
from agentkit.release.seed import _assert_fk_closed, _insert_row

_PROMOTABLE = {"exported", "imported"}

# facts.* columns copied straight from the surviving staging rows.
_FACTS_COLUMNS = {
    "document": ["doc_id", "kind", "title", "url", "division", "sha256", "page_count"],
    "product": ["product_id", "family_id", "model_name", "variant", "description",
                "attributes", "source_doc_id", "source_locator"],
    "capability_row": ["cap_id", "family_id", "comp_type", "lubricated", "cooling",
                       "capacity_min", "capacity_max", "capacity_unit",
                       "discharge_p_min", "discharge_p_max", "pressure_unit",
                       "driver", "standards", "source_doc_id", "source_locator"],
    "company_fact": ["fact_id", "kind", "value", "detail", "source_doc_id", "source_locator"],
    "office": ["office_id", "name", "city", "region", "address", "phone", "email",
               "serves_divisions", "source_doc_id", "source_locator"],
}


def _surviving(conn: Connection, rc: str, table: str) -> list:
    return conn.execute(
        text(f"SELECT * FROM staging.{table} WHERE release_candidate_id = :rc "
             "AND review_status IN ('approved', 'edited')"),
        {"rc": rc},
    ).mappings().all()


def _allocate_release_id(conn: Connection, now: datetime) -> str:
    prefix = f"r{now.year}.{now.month:02d}"
    ids = conn.execute(
        text("SELECT release_id FROM facts.release WHERE release_id LIKE :p"),
        {"p": prefix + ".%"},
    ).scalars().all()
    max_n = 0
    for rid in ids:
        tail = rid[len(prefix) + 1:]
        if tail.isdigit():
            max_n = max(max_n, int(tail))
    return f"{prefix}.{max_n + 1}"


def promote(
    conn: Connection,
    rc_id: str,
    *,
    client: str,
    release_id: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Promote an RC's surviving rows into a new ``facts.*`` release (inactive)."""
    ledger = get_rc(conn, rc_id)
    if ledger is None:
        raise ValueError(f"cannot promote {rc_id}: no release_candidate ledger row")
    if ledger["status"] not in _PROMOTABLE:
        raise ValueError(
            f"cannot promote {rc_id}: status {ledger['status']!r} not in {sorted(_PROMOTABLE)}")

    now = now or datetime.now(timezone.utc)
    release_id = release_id or _allocate_release_id(conn, now)

    documents = _surviving(conn, rc_id, "document")
    products = _surviving(conn, rc_id, "product")
    caps = _surviving(conn, rc_id, "capability_row")
    gases = _surviving(conn, rc_id, "capability_gas")
    company_facts = _surviving(conn, rc_id, "company_fact")
    offices = _surviving(conn, rc_id, "office")

    surviving_caps = {c["cap_id"] for c in caps}
    doc_ids = [d["doc_id"] for d in documents]
    counts: dict[str, int] = {}

    # facts.release (inactive until `activate`)
    _insert_row(conn, "release", {
        "release_id": release_id,
        "built_at": now,
        "source_manifest": {"rc": rc_id, "documents": doc_ids},
        "is_active": False,
        "notes": f"Promoted from {rc_id}",
    }, release_id=None)
    counts["release"] = 1

    def copy(table: str, rows: list) -> None:
        for r in rows:
            row = {c: r[c] for c in _FACTS_COLUMNS[table]}
            _insert_row(conn, table, row, release_id=release_id)
        counts[table] = len(rows)

    copy("document", documents)

    # product_family from the frozen families.yaml — only families referenced by a
    # surviving product/capability_row, with source parsed into doc/locator.
    families = {f["id"]: f for f in load_families(client)}
    referenced = {r["family_id"] for r in (*products, *caps) if r.get("family_id")}
    fam_count = 0
    for fid in sorted(referenced):
        fam = families.get(fid)
        if not fam:
            raise ValueError(f"promote: family {fid!r} referenced but not in families.yaml")
        src_doc, src_loc = parse_source(fam.get("source"))
        _insert_row(conn, "product_family", {
            "family_id": fid,
            "division": fam.get("division"),
            "category": fam.get("category"),
            "name": fam.get("name"),
            "summary": fam.get("summary"),
            "applications": fam.get("applications") or [],
            "standards": fam.get("standards") or [],
            "source_doc_id": src_doc,
            "source_locator": src_loc,
        }, release_id=release_id)
        fam_count += 1
    counts["product_family"] = fam_count

    copy("product", products)
    copy("capability_row", caps)

    surviving_gases = [g for g in gases if g["cap_id"] in surviving_caps]
    for g in surviving_gases:
        _insert_row(conn, "capability_gas",
                    {"cap_id": g["cap_id"], "gas": g["gas"]}, release_id=release_id)
    counts["capability_gas"] = len(surviving_gases)

    copy("company_fact", company_facts)
    copy("office", offices)

    # region_state from the curated seed — only entries whose office is in this release.
    promoted_offices = {o["office_id"] for o in offices}
    rs_count = 0
    for entry in load_region_states(client):
        if entry.get("office_id") in promoted_offices:
            _insert_row(conn, "region_state", {
                "state": entry["state"], "region": entry["region"],
                "office_id": entry["office_id"],
            }, release_id=release_id)
            rs_count += 1
    counts["region_state"] = rs_count

    _assert_fk_closed(conn, release_id)
    set_status(conn, rc_id, "promoted")
    return {"release_id": release_id, "counts": counts}


def activate(conn: Connection, release_id: str) -> None:
    """Make ``release_id`` the single active release (partial unique index enforces one)."""
    exists = conn.execute(
        text("SELECT 1 FROM facts.release WHERE release_id = :r"), {"r": release_id}
    ).scalar_one_or_none()
    if exists is None:
        raise ValueError(f"cannot activate unknown release {release_id!r}")
    conn.execute(text("UPDATE facts.release SET is_active = false WHERE is_active"))
    conn.execute(
        text("UPDATE facts.release SET is_active = true WHERE release_id = :r"),
        {"r": release_id},
    )
