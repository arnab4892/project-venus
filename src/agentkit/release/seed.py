"""Demo seed loader (framework-generic; the data lives under ``clients/<id>/seeds``).

``seed_demo(conn, client)`` loads ``clients/<client>/seeds/demo.yaml`` into
``facts.*`` under its release, marks that release active (deactivating any other),
and asserts FK closure at the end. It is:

* **idempotent** — it deletes the release's existing rows first, then re-inserts;
* **transaction-neutral** — it runs on the caller's ``Connection`` and never
  commits, so tests can roll it back and the CLI can wrap it in one commit;
* **client-agnostic** — no client id is hard-coded; the single ``clients/*``
  directory is auto-detected unless ``client`` is given.

Only the seed writes to ``facts.*`` in this milestone (no LLM output, no ops rows).
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from sqlalchemy import Connection, text

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CLIENTS_DIR = _REPO_ROOT / "clients"

# Columns needing an explicit cast when bound as a single parameter.
_JSONB_COLUMNS = {"source_manifest", "detail", "attributes"}
_ARRAY_COLUMNS = {
    "applications", "standards", "driver", "serves_divisions",
    "heading_path", "family_ids",
}

# (yaml section, facts table) in FK-safe insert order. `capability_gas` is nested
# under each capability_row as `gases` and handled separately.
_SECTIONS = [
    ("documents", "document"),
    ("product_families", "product_family"),
    ("products", "product"),
    ("capability_rows", "capability_row"),
    ("company_facts", "company_fact"),
    ("offices", "office"),
    ("region_states", "region_state"),
]

# child-before-parent delete order (for idempotency)
_DELETE_ORDER = [
    "capability_gas", "region_state", "chunk", "product", "capability_row",
    "company_fact", "office", "product_family", "document", "release",
]

# child table -> list of (fk_columns, parent_table, parent_key_columns) for the
# end-of-seed FK-closure assertion (same-release).
_FK_CHECKS: dict[str, list[tuple[list[str], str, list[str]]]] = {
    "product_family": [(["source_doc_id"], "document", ["doc_id"])],
    "product": [
        (["family_id"], "product_family", ["family_id"]),
        (["source_doc_id"], "document", ["doc_id"]),
    ],
    "capability_row": [
        (["family_id"], "product_family", ["family_id"]),
        (["source_doc_id"], "document", ["doc_id"]),
    ],
    "capability_gas": [(["cap_id"], "capability_row", ["cap_id"])],
    "company_fact": [(["source_doc_id"], "document", ["doc_id"])],
    "office": [(["source_doc_id"], "document", ["doc_id"])],
    "region_state": [(["office_id"], "office", ["office_id"])],
}


def _autodetect_client() -> str:
    clients = sorted(p.name for p in _CLIENTS_DIR.iterdir() if p.is_dir())
    if len(clients) == 1:
        return clients[0]
    raise ValueError(
        f"Cannot auto-detect client from {clients!r}; pass an explicit client id."
    )


def _insert_row(conn: Connection, table: str, row: dict, *, release_id: str | None) -> None:
    cols: list[str] = []
    placeholders: list[str] = []
    params: dict[str, object] = {}
    for col, value in row.items():
        cols.append(col)
        if col in _JSONB_COLUMNS:
            placeholders.append(f"CAST(:{col} AS jsonb)")
            params[col] = json.dumps(value) if value is not None else None
        elif col in _ARRAY_COLUMNS:
            placeholders.append(f"CAST(:{col} AS text[])")
            params[col] = value
        else:
            placeholders.append(f":{col}")
            params[col] = value
    if release_id is not None:
        cols.append("release_id")
        placeholders.append(":release_id")
        params["release_id"] = release_id
    sql = f"INSERT INTO facts.{table} ({', '.join(cols)}) VALUES ({', '.join(placeholders)})"
    conn.execute(text(sql), params)


def _delete_release(conn: Connection, release_id: str) -> None:
    for table in _DELETE_ORDER:
        conn.execute(
            text(f"DELETE FROM facts.{table} WHERE release_id = :rid"), {"rid": release_id}
        )


def _assert_fk_closed(conn: Connection, release_id: str) -> None:
    for child, checks in _FK_CHECKS.items():
        for fk_cols, parent, parent_keys in checks:
            join = " AND ".join(f"p.{pk} = c.{fk}" for pk, fk in zip(parent_keys, fk_cols))
            orphans = conn.execute(
                text(
                    f"SELECT count(*) FROM facts.{child} c "
                    f"WHERE c.release_id = :rid AND NOT EXISTS ("
                    f"  SELECT 1 FROM facts.{parent} p "
                    f"  WHERE p.release_id = :rid AND {join})"
                ),
                {"rid": release_id},
            ).scalar_one()
            if orphans:
                raise AssertionError(
                    f"FK closure failed: {orphans} row(s) in facts.{child} "
                    f"reference a missing facts.{parent} ({fk_cols} -> {parent_keys})"
                )


def seed_demo(conn: Connection, client: str | None = None, *, activate: bool = True) -> dict[str, int]:
    """Load the demo seed for ``client`` into ``facts.*``. Returns per-table row counts."""
    client = client or _autodetect_client()
    seed_path = _CLIENTS_DIR / client / "seeds" / "demo.yaml"
    data = yaml.safe_load(seed_path.read_text())

    release = dict(data["release"])
    release_id = release["release_id"]

    _delete_release(conn, release_id)

    if activate:
        conn.execute(text("UPDATE facts.release SET is_active = false WHERE is_active"))
        release["is_active"] = True

    counts: dict[str, int] = {}
    _insert_row(conn, "release", release, release_id=None)
    counts["release"] = 1

    for section, table in _SECTIONS:
        rows = data.get(section, []) or []
        for row in rows:
            row = dict(row)
            gases = row.pop("gases", None)
            _insert_row(conn, table, row, release_id=release_id)
            if table == "capability_row" and gases:
                for gas in gases:
                    conn.execute(
                        text(
                            "INSERT INTO facts.capability_gas (cap_id, release_id, gas) "
                            "VALUES (:cap, :rid, :gas)"
                        ),
                        {"cap": row["cap_id"], "rid": release_id, "gas": gas},
                    )
                counts["capability_gas"] = counts.get("capability_gas", 0) + len(gases)
        counts[table] = len(rows)

    _assert_fk_closed(conn, release_id)
    return counts
