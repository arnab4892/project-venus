"""Shared agent scaffolding (LLD-AG).

An :class:`AgentOutput` is what every agent returns to the orchestrator; the orchestrator
persists the invocation, runs the grounding gate for ``answer`` actions, and emits messages.
:func:`render_tool_context` turns tool records into a compact, cite-able block for the compose
LLM, and :func:`compose_grounded_answer` is the shared "write the answer from these tool
results, citing their ids" step used by the application and FAQ agents.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from agentkit.extract.llm import CompleteFn, call_json
from agentkit.runtime.language import respond_in
from agentkit.runtime.ops import MessageRecord, ToolCallRecord
from agentkit.runtime.schemas import FAQ_SCHEMA
from agentkit.runtime.triage import build_chat_messages

_TR_RE = re.compile(r"tr\d+")


@dataclass
class AgentOutput:
    """One agent's result. ``answer`` carries a draft + cited tr ids (grounded downstream);
    every other action carries final ``messages`` directly."""

    action: str  # answer | ask_slot | handoff | deflect | clarify
    draft_text: str = ""
    citations: list[str] = field(default_factory=list)
    messages: list[MessageRecord] = field(default_factory=list)
    slots: dict | None = None
    output: dict | None = None


def _truncate(s: str, n: int = 600) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + "…"


def render_tool_context(records: list[ToolCallRecord]) -> str:
    """A compact, cite-able rendering of tool results (ids, published numbers, locators)."""
    lines: list[str] = []
    for rec in records:
        r = rec.result
        if rec.tool == "match_capability":
            parts = []
            for m in r.get("matches", []):
                seg = (
                    f"{m.get('family_name')} ({m['family_id']}, {m['cap_id']}): "
                    f"capacity up to {m.get('capacity_max')} {m.get('capacity_unit')}, "
                    f"discharge up to {m.get('discharge_p_max')} {m.get('pressure_unit')}"
                )
                if m.get("standards"):
                    seg += f", standards {m['standards']}"
                if m.get("near_edge"):
                    seg += " [near a published limit]"
                parts.append(seg)
            lines.append(
                f"[{rec.tr_id}] match_capability matches :: "
                + (" | ".join(parts) if parts else "no in-range published family")
            )
            nc = r.get("non_comparable_candidates", [])
            if nc:
                lines.append(
                    f"[{rec.tr_id}] non-comparable (cannot confirm fit) :: "
                    + "; ".join(f"{c.get('family_name')} — {c.get('reason')}" for c in nc)
                )
        elif rec.tool == "search_documents":
            for c in r.get("chunks", []):
                lines.append(
                    f"[{rec.tr_id}] search_documents chunk={c['chunk_id']} "
                    f"locator={c.get('locator')!r} url={c.get('url')} :: {_truncate(c.get('content_md'))}"
                )
        elif rec.tool == "get_company_fact":
            facts = "; ".join(
                f"{f['fact_id']}={f.get('value')!r} (loc {f.get('source_locator')})"
                for f in r.get("facts", [])
            )
            lines.append(f"[{rec.tr_id}] get_company_fact {rec.args.get('kind')} :: {facts}")
        elif rec.tool in ("get_product", "list_products"):
            prods = "; ".join(
                f"{p.get('product_id') or p.get('family_id')}={p.get('display_name') or p.get('name')!r}"
                for p in r.get("products", [])
            )
            lines.append(f"[{rec.tr_id}] {rec.tool} :: {prods}")
        elif rec.tool == "get_office":
            o = r.get("office") or {}
            lines.append(f"[{rec.tr_id}] get_office :: {o.get('office_id')} {o.get('city')}")
        else:
            lines.append(f"[{rec.tr_id}] {rec.tool} :: {_truncate(json.dumps(r, default=str))}")
    return "\n".join(lines) if lines else "(no tool results)"


def normalise_citations(raw: list[str]) -> list[str]:
    """Extract ``trN`` ids from whatever the model put in `citations` (e.g. "[tr1]")."""
    out: list[str] = []
    for item in raw or []:
        for m in _TR_RE.findall(str(item)):
            if m not in out:
                out.append(m)
    return out


def compose_grounded_answer(
    *,
    complete: CompleteFn,
    prompt_body: str,
    history: list[dict],
    latest_user: str,
    records: list[ToolCallRecord],
    language: str,
    extra_instruction: str = "",
) -> tuple[str, list[str]]:
    """Ask the runtime LLM to write the answer from the tool results, citing their tr ids.

    Returns ``(message, cited_tr_ids)``. The grounding gate (LLD-RT-05) validates the result
    afterwards; this only drafts it. Numbers must be quoted from the tool block, never invented.
    """
    system = (
        prompt_body
        + "\n\n" + respond_in(language)
        + "\n\nUse ONLY the tool results below. Quote figures exactly as they appear; never "
        "invent a number, name or date. Put the tool_result ids (trN) you relied on in "
        "`citations`.\n\nTool results:\n" + render_tool_context(records)
    )
    if extra_instruction:
        system += "\n\n" + extra_instruction
    messages = build_chat_messages(system, history, latest_user)
    raw = call_json(
        client=None,
        complete=complete,
        messages=messages,
        json_schema=FAQ_SCHEMA,
        schema_name="answer",
        kind="compose",
        section_id=None,
    )
    return raw.get("message", ""), normalise_citations(raw.get("citations", []))
