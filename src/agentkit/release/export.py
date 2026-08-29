"""Review export (LLD-REL-01): one xlsx, one sheet per staging table.

Each sheet lists a staging table's rows for the RC with, for every value column,
the **evidence quote beside it**, plus provenance (source doc + locator),
confidence, the ``conflict_group`` / ``needs_family`` markers, the current
``review_status`` and an empty **reviewer-decision** column with a dropdown
(approve / edit / reject). The header row is frozen and columns are sized.

Runs on the caller's ``Connection`` (reads only) and advances the RC to
``exported`` (the caller owns the commit).
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import Connection, text

from agentkit.release.candidate import set_status

# Per table: natural id column, then (value_column, evidence_key) pairs. An
# evidence_key of None means the column has no single supporting quote.
_SHEETS: dict[str, tuple[str, list[tuple[str, str | None]]]] = {
    "document": ("doc_id", [
        ("kind", None), ("title", None), ("url", None),
        ("division", None), ("sha256", None), ("page_count", None),
    ]),
    "product_family": ("family_id", [
        ("division", None), ("category", None), ("name", "name"),
        ("summary", "summary"), ("applications", "applications"), ("standards", "standards"),
    ]),
    "product": ("product_id", [
        ("family_id", "family"), ("model_name", "model_name"), ("variant", "variant"),
        ("description", "description"), ("attributes", "attributes"),
    ]),
    "capability_row": ("cap_id", [
        ("family_id", "family"), ("comp_type", "comp_type"), ("lubricated", "lubricated"),
        ("cooling", "cooling"),
        ("capacity_min", "capacity"), ("capacity_max", "capacity"), ("capacity_unit", "capacity"),
        ("discharge_p_min", "discharge_pressure"), ("discharge_p_max", "discharge_pressure"),
        ("pressure_unit", "discharge_pressure"),
        ("driver", "drivers"), ("standards", "standards"),
    ]),
    "capability_gas": ("cap_id", [("gas", "gas")]),
    "company_fact": ("fact_id", [
        ("kind", "kind"), ("value", "value"), ("detail", "detail"),
    ]),
    "office": ("office_id", [
        ("name", "name"), ("city", "city"), ("region", None), ("address", "address"),
        ("phone", "phone"), ("email", "email"), ("serves_divisions", "serves_divisions"),
    ]),
}

# Trailing provenance / review columns common to every sheet.
_META_COLUMNS = [
    "source_doc_id", "source_locator", "section_id",
    "confidence", "conflict_group", "needs_family", "review_status",
]
_DECISION_COL = "reviewer_decision"
_DECISION_OPTIONS = '"approve,edit,reject"'


def _is_blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, tuple, dict, str)):
        return len(value) == 0
    return False


def _cell(value: object) -> object:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) or None
    if isinstance(value, dict):
        # Skip empty-valued entries so e.g. {"printed": []} renders blank, not "printed=[]".
        parts = [f"{k}={_cell(v)}" for k, v in value.items() if not _is_blank(v)]
        return "; ".join(parts) or None
    return value


def _evidence_quote(evidence: object, key: str | None) -> object:
    if key is None or not isinstance(evidence, dict):
        return None
    return _cell(evidence.get(key))


def _headers(value_cols: list[tuple[str, str | None]]) -> list[str]:
    headers: list[str] = []
    for col, ekey in value_cols:
        headers.append(col)
        if ekey is not None:
            headers.append(f"{col}»evidence")
    return headers


def _write_sheet(ws: Worksheet, conn: Connection, rc_id: str, table: str) -> int:
    natural_id, value_cols = _SHEETS[table]
    headers = [natural_id, *_headers(value_cols), *_META_COLUMNS, _DECISION_COL]
    ws.append(headers)

    rows = conn.execute(
        text(f"SELECT * FROM staging.{table} WHERE release_candidate_id = :rc "
             f"ORDER BY {natural_id}"),
        {"rc": rc_id},
    ).mappings().all()

    # capability_gas has no source columns of its own (provenance is the parent
    # capability_row's, by design). Fill the sheet's provenance display-only.
    parent_prov: dict[str, tuple[object, object]] = {}
    if table == "capability_gas":
        for pr in conn.execute(
            text("SELECT cap_id, source_doc_id, source_locator FROM staging.capability_row "
                 "WHERE release_candidate_id = :rc"),
            {"rc": rc_id},
        ).mappings():
            parent_prov[pr["cap_id"]] = (pr["source_doc_id"], pr["source_locator"])

    for r in rows:
        evidence = r.get("evidence")
        line: list[object] = [r.get(natural_id)]
        for col, ekey in value_cols:
            line.append(_cell(r.get(col)))
            if ekey is not None:
                line.append(_evidence_quote(evidence, ekey))
        meta_vals = {m: r.get(m) for m in _META_COLUMNS}
        if table == "capability_gas":
            sd, sl = parent_prov.get(r.get("cap_id"), (None, None))
            meta_vals["source_doc_id"] = sd
            meta_vals["source_locator"] = sl
        line += [_cell(meta_vals[m]) for m in _META_COLUMNS]
        line.append(None)  # empty reviewer decision
        ws.append(line)

    # Freeze the header, size columns, attach the decision dropdown.
    ws.freeze_panes = "A2"
    for idx, header in enumerate(headers, start=1):
        letter = ws.cell(row=1, column=idx).column_letter
        width = 40 if "evidence" in header or header in ("url", "address", "summary") else 18
        ws.column_dimensions[letter].width = width

    decision_letter = ws.cell(row=1, column=len(headers)).column_letter
    dv = DataValidation(type="list", formula1=_DECISION_OPTIONS, allow_blank=True)
    dv.error = "Choose approve, edit, or reject."
    dv.prompt = "Reviewer decision"
    ws.add_data_validation(dv)
    if rows:
        dv.add(f"{decision_letter}2:{decision_letter}{len(rows) + 1}")
    return len(rows)


def export_rc(conn: Connection, rc_id: str, out_path: Path) -> dict[str, int]:
    """Write the review workbook for ``rc_id`` and mark the RC ``exported``."""
    wb = Workbook()
    wb.remove(wb.active)  # drop the default empty sheet
    counts: dict[str, int] = {}
    for table in _SHEETS:
        ws = wb.create_sheet(title=table)
        counts[table] = _write_sheet(ws, conn, rc_id, table)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    set_status(conn, rc_id, "exported")
    return counts
