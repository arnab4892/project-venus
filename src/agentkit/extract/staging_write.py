"""Write extracted rows to ``staging.*`` under a release candidate (LLD-EXT-10).

Never writes to ``facts.*``. Runs on the caller's ``Connection`` and never commits
(mirrors :mod:`agentkit.ingest.staging_write`).

**RC idempotency (per RC).** Re-running extraction for the same RC must replace
that RC's *pending* rows only — rows a reviewer has already ``approved`` /
``edited`` / ``rejected`` (via a future import) are never overwritten. This is
achieved by deleting the RC's ``pending`` rows first, then inserting the fresh
extraction with ``ON CONFLICT DO NOTHING``: any surviving non-pending row keeps
its PK, so the insert leaves it untouched.
"""

from __future__ import annotations

import json

from sqlalchemy import Connection, text

# Columns needing an explicit cast when bound as a single parameter.
_JSONB_COLUMNS = {"evidence", "attributes", "detail"}
_ARRAY_COLUMNS = {"driver", "standards", "serves_divisions"}

# The staging content tables this milestone writes (document identity + six
# LLM-extracted content tables). No mirror for release / region_state.
STAGING_TABLES = (
    "document", "product_family", "product", "capability_row",
    "capability_gas", "company_fact", "office",
)


def _insert(conn: Connection, table: str, row: dict, *, rc_id: str) -> None:
    cols = ["release_candidate_id"]
    placeholders = [":release_candidate_id"]
    params: dict[str, object] = {"release_candidate_id": rc_id}
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
    sql = (
        f"INSERT INTO staging.{table} ({', '.join(cols)}) "
        f"VALUES ({', '.join(placeholders)}) ON CONFLICT DO NOTHING"
    )
    conn.execute(text(sql), params)


def clear_pending(conn: Connection, rc_id: str, *, tables: tuple[str, ...] = STAGING_TABLES) -> None:
    """Delete this RC's ``pending`` rows (reviewed rows are preserved)."""
    for table in tables:
        conn.execute(
            text(
                f"DELETE FROM staging.{table} "
                "WHERE release_candidate_id = :rc AND review_status = 'pending'"
            ),
            {"rc": rc_id},
        )


def write_rows(
    conn: Connection,
    *,
    rc_id: str,
    document_rows: list[dict],
    content_rows: list[tuple[str, dict]],
) -> dict[str, int]:
    """Replace the RC's pending rows with a fresh extraction. Returns per-table counts.

    ``document_rows`` are ``staging.document`` identity rows; ``content_rows`` are
    ``(table, row)`` tuples from the extractor. Reviewed (non-pending) rows are
    left intact by the delete-pending-then-insert-or-ignore strategy.
    """
    clear_pending(conn, rc_id)

    counts: dict[str, int] = {t: 0 for t in STAGING_TABLES}
    for row in document_rows:
        _insert(conn, "document", dict(row), rc_id=rc_id)
        counts["document"] += 1
    for table, row in content_rows:
        _insert(conn, table, dict(row), rc_id=rc_id)
        counts[table] += 1
    return counts
