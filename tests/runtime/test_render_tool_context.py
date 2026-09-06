"""render_tool_context: get_product family-envelope rendering (LLD-TOOL-03 ext / LLD-RT-05).

A capability-only family's published envelope must reach the compose prompt as text — figure and
unit inseparable, via ``format_number`` — so the compose can state the limit and the numeric guard
can source it. A family that publishes no envelope renders gracefully (name/summary, no figures).
"""

from __future__ import annotations

from agentkit.runtime.agents.base import render_tool_context
from agentkit.runtime.ops import ToolCallRecord


def _rec(result: dict) -> ToolCallRecord:
    return ToolCallRecord(
        tr_id="tr1", tool="get_product", args={}, result=result, rows_returned=1, latency_ms=1
    )


def test_envelope_renders_figure_with_unit_inseparable():
    out = render_tool_context([_rec({
        "matched_by": "family_envelope", "products": [],
        "family": {"family_id": "fam.process_recip", "family_name": "Process Compressors (Recip.)"},
        "capabilities": [{
            "cap_id": "cap.process.0", "comp_type": "Reciprocating",
            "capacity_max": 25000, "capacity_unit": "Nm3/hr",
            "discharge_p_max": 1000, "pressure_unit": "barg",
            "standards": ["API-618 or equivalent"], "gases": ["hydrogen"],
        }],
    })])
    assert "25,000 Nm3/hr" in out          # Indian-grouped by format_number (≥10k), unit adjacent
    assert "1000 barg" in out               # <10k: no grouping, but still unit-adjacent
    assert "fam.process_recip" in out       # citable id present
    assert "API-618 or equivalent" in out


def test_empty_envelope_renders_without_figures():
    out = render_tool_context([_rec({
        "matched_by": "family_envelope", "products": [],
        "family": {"family_id": "fam.h2_fuelling", "family_name": "Hydrogen Fuelling System",
                   "summary": "Quick-fill hydrogen dispensing systems."},
        "capabilities": [],
    })])
    assert "fam.h2_fuelling" in out
    assert "capacity up to" not in out      # no fabricated figures
    assert "Quick-fill hydrogen dispensing" in out
