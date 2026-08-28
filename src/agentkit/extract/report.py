"""Human-facing run report for extraction (LLD-EXT).

Summarises what a run did so the two human gates are informed: after pass 1 the
proposed family list; after pass 2 the per-table row counts, evidence-dropped
fields (with examples), every ``needs_family`` and ``conflict_group`` row, and the
API token/cost totals. The totals are surfaced here (and in the milestone note),
not left only in the gitignored ``llm-calls.jsonl``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentkit.extract.evidence import Drop
from agentkit.extract.staging_write import STAGING_TABLES

_DROP_EXAMPLES = 8
_FLAG_ROWS = 50


def _fmt_totals(totals: dict) -> list[str]:
    cost = totals.get("cost_usd")
    cost_str = "n/a (no price configured)" if cost is None else f"${cost:.4f}"
    cached = totals.get("tokens_cached", 0)
    hit_rate = totals.get("cache_hit_rate", 0.0)
    return [
        f"  LLM calls        : {totals.get('calls', 0)} "
        f"(skipped {totals.get('skipped', 0)})",
        f"  tokens in / out  : {totals.get('tokens_in', 0)} / {totals.get('tokens_out', 0)} "
        f"(total {totals.get('tokens_total', 0)})",
        f"  prompt cache     : {cached} cached input tokens ({hit_rate:.0%} hit rate)",
        f"  est. API cost    : {cost_str}",
    ]


@dataclass
class FamiliesReport:
    """Pass-1 gate report: the proposed families + token totals."""

    families: list[dict]
    totals: dict
    families_path: str

    def summary_lines(self) -> list[str]:
        lines = [
            "── Pass 1: family discovery ─────────────────────────────",
            f"Proposed {len(self.families)} families → {self.families_path}",
            "STOP: review/edit that file, then re-run `extract run` to continue.",
            "",
        ]
        for f in self.families:
            lines.append(f"  {f.get('id'):24} {f.get('division'):12} {f.get('name')}")
        lines.append("")
        lines += _fmt_totals(self.totals)
        return lines


@dataclass
class ExtractReport:
    """Pass-2 gate report: staged counts, drops, flags, token/cost totals."""

    rc_id: str
    counts: dict[str, int]
    drops: list[Drop]
    skipped_sections: list[str]
    rows: list[tuple[str, dict]]
    totals: dict
    family_count: int = 0
    _needs_family: list[dict] = field(default_factory=list)
    _conflicts: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._needs_family = [r for _, r in self.rows if r.get("needs_family")]
        self._conflicts = [r for _, r in self.rows if r.get("conflict_group")]

    def summary_lines(self) -> list[str]:
        lines = [
            f"── Extraction complete → {self.rc_id} ───────────────────",
            f"Frozen families: {self.family_count}",
            "Per-table staged rows (review_status=pending):",
        ]
        for table in STAGING_TABLES:
            lines.append(f"  staging.{table:16} {self.counts.get(table, 0)}")
        if self.skipped_sections:
            lines.append(f"Sections skipped (schema-invalid ×{len(self.skipped_sections)}): "
                         + ", ".join(self.skipped_sections[:10]))

        lines.append("")
        lines.append(f"Evidence-dropped fields: {len(self.drops)}")
        for d in self.drops[:_DROP_EXAMPLES]:
            lines.append(f"  [{d.section_id}] {d.path} = {d.value!r} "
                         f"(evidence {d.evidence!r} not verbatim)")
        if len(self.drops) > _DROP_EXAMPLES:
            lines.append(f"  … and {len(self.drops) - _DROP_EXAMPLES} more")

        lines.append("")
        lines.append(f"needs_family rows: {len(self._needs_family)}")
        for r in self._needs_family[:_FLAG_ROWS]:
            nid = r.get("cap_id") or r.get("product_id") or "?"
            lines.append(f"  {nid} [{r.get('section_id')}] {r.get('source_locator')}")

        lines.append("")
        lines.append(f"conflict_group rows: {len(self._conflicts)}")
        for r in self._conflicts[:_FLAG_ROWS]:
            lines.append(f"  {r.get('conflict_group')} :: {r.get('cap_id')} "
                         f"capacity_max={r.get('capacity_max')} [{r.get('source_locator')}]")

        lines.append("")
        lines += _fmt_totals(self.totals)
        return lines
