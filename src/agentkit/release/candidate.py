"""Release-candidate lifecycle ledger (milestone 3a).

A minimal ledger in ``staging.release_candidate``: ``id, client, created_at,
status`` with ``status ∈ {open, exported, imported, promoted, abandoned}``.
``release create`` allocates the next ``rc.<client>.NNNN``; ``release export``
advances it to ``exported``; import/promote (milestone 3b) advance it further.

Runs on the caller's ``Connection`` and never commits (mirrors the seed loader).

Design note: the staging content mirrors carry NO foreign key to this ledger (see
migration ``0002`` / LLD-DB-06 clarification). RC validity is a **release-gate**
concern, not a staging invariant — ``release promote`` (3b) must refuse any RC
with no ledger row or whose status is not ``exported``/``imported``. The ingest
bootstrap RC (``rc.<client>.bootstrap``) is deliberately not a ledger row and
stays valid for ingest only.
"""

from __future__ import annotations

import re

from sqlalchemy import Connection, text

RC_STATUSES = ("open", "exported", "imported", "promoted", "abandoned")

_RC_NUM_RE = re.compile(r"^rc\.(?P<client>.+)\.(?P<num>\d{4})$")


def _next_number(conn: Connection, client: str) -> int:
    rows = conn.execute(
        text("SELECT id FROM staging.release_candidate WHERE client = :c"), {"c": client}
    ).scalars()
    highest = 0
    for rc_id in rows:
        m = _RC_NUM_RE.match(rc_id)
        if m and m.group("client") == client:
            highest = max(highest, int(m.group("num")))
    return highest + 1


def create_rc(conn: Connection, client: str) -> str:
    """Allocate and insert the next ``rc.<client>.NNNN`` (status ``open``)."""
    rc_id = f"rc.{client}.{_next_number(conn, client):04d}"
    conn.execute(
        text(
            "INSERT INTO staging.release_candidate (id, client, status) "
            "VALUES (:id, :client, 'open')"
        ),
        {"id": rc_id, "client": client},
    )
    return rc_id


def list_rc(conn: Connection, client: str | None = None) -> list[dict]:
    """All release candidates (optionally for one client), newest first."""
    sql = "SELECT id, client, created_at, status FROM staging.release_candidate"
    params: dict[str, object] = {}
    if client:
        sql += " WHERE client = :c"
        params["c"] = client
    sql += " ORDER BY created_at DESC, id DESC"
    return [dict(r) for r in conn.execute(text(sql), params).mappings()]


def get_rc(conn: Connection, rc_id: str) -> dict | None:
    row = conn.execute(
        text("SELECT id, client, created_at, status FROM staging.release_candidate WHERE id = :id"),
        {"id": rc_id},
    ).mappings().first()
    return dict(row) if row else None


def set_status(conn: Connection, rc_id: str, status: str) -> None:
    """Advance an RC's lifecycle status (validated against ``RC_STATUSES``)."""
    if status not in RC_STATUSES:
        raise ValueError(f"unknown RC status {status!r}; expected one of {RC_STATUSES}")
    result = conn.execute(
        text("UPDATE staging.release_candidate SET status = :s WHERE id = :id"),
        {"s": status, "id": rc_id},
    )
    if result.rowcount == 0:
        raise ValueError(f"no release candidate {rc_id!r} in the ledger")
