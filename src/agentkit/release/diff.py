"""Diff an RC's surviving capability rows against the active release (LLD-REL-04).

Any changed numeric in ``capability_row`` is listed so a human acknowledges it
before promote. Compares by natural id (``cap_id``) against ``facts.active_
capability_row`` — so it handles the first real promote (active release is only
the seed/demo, natural ids barely overlap → everything is "new") without assuming
zero overlap: matching ids are compared field-by-field, the rest are reported as
new or dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Connection, text

_NUMERIC_FIELDS = ("capacity_min", "capacity_max", "discharge_p_min", "discharge_p_max")


@dataclass
class DiffReport:
    rc_id: str
    active_release_id: str | None
    new_caps: list[str] = field(default_factory=list)
    changed: list[tuple[str, str, object, object]] = field(default_factory=list)
    dropped_caps: list[str] = field(default_factory=list)
    matched: int = 0

    @property
    def everything_new(self) -> bool:
        # No surviving row overlapped an active row — a clean first promote. The
        # active release's own rows being superseded (dropped_caps) is expected.
        return self.matched == 0


def _num(v: object) -> float | None:
    return None if v is None else float(v)


def diff_rc(conn: Connection, rc_id: str) -> DiffReport:
    active_release_id = conn.execute(
        text("SELECT release_id FROM facts.release WHERE is_active")
    ).scalar_one_or_none()

    surviving = {
        r["cap_id"]: r
        for r in conn.execute(
            text("SELECT * FROM staging.capability_row WHERE release_candidate_id = :rc "
                 "AND review_status IN ('approved', 'edited')"),
            {"rc": rc_id},
        ).mappings()
    }
    active = {
        r["cap_id"]: r
        for r in conn.execute(text("SELECT * FROM facts.active_capability_row")).mappings()
    }

    report = DiffReport(rc_id, active_release_id)
    for cap_id, row in sorted(surviving.items()):
        if cap_id not in active:
            report.new_caps.append(cap_id)
            continue
        report.matched += 1
        old = active[cap_id]
        for f in _NUMERIC_FIELDS:
            if _num(row.get(f)) != _num(old.get(f)):
                report.changed.append((cap_id, f, old.get(f), row.get(f)))
    report.dropped_caps = sorted(set(active) - set(surviving))
    return report


def format_diff(r: DiffReport) -> str:
    active = r.active_release_id or "(none)"
    lines = [f"release diff {r.rc_id} vs active release {active}:"]
    if r.everything_new and r.new_caps:
        lines.append(f"  everything is new — {len(r.new_caps)} capability rows, "
                     "0 changed numerics (no overlap with the active release).")
        if r.dropped_caps:
            lines.append(f"  ({len(r.dropped_caps)} active rows superseded by the new release)")
    else:
        lines.append(f"  {len(r.new_caps)} new capability rows")
        lines.append(f"  {len(r.changed)} changed numerics (acknowledge before promote):")
        for cap_id, f, old, new in r.changed:
            lines.append(f"    ~ {cap_id}.{f}: {old} → {new}")
        if r.dropped_caps:
            lines.append(f"  {len(r.dropped_caps)} active rows not carried forward: "
                         + ", ".join(r.dropped_caps))
    return "\n".join(lines)
