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
from agentkit.runtime.format import format_number
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
    # Structured messages (e.g. a document_card) appended AFTER a grounded text answer. They
    # ride the answer path so the grounding gate still guards the turn: if grounding fails, the
    # extras are dropped with the drafted text and only the fallback ships (LLD-AG-03).
    extra_messages: list[MessageRecord] = field(default_factory=list)


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
                    f"{m.get('family_name')}: "
                    f"capacity up to {format_number(m.get('capacity_max'))} {m.get('capacity_unit')}, "
                    f"discharge up to {format_number(m.get('discharge_p_max'))} {m.get('pressure_unit')}"
                )
                if m.get("standards"):
                    seg += f", standards {', '.join(m['standards'])}"
                cv = m.get("converted_capacity")
                if cv:
                    # Tool-computed unit reconciliation (LLD-TOOL-01, Fix 3): the compose may state
                    # this converted figure, never derive one (persona no-arithmetic rule).
                    seg += (
                        f" (the requested duty in {cv['from']} converts to "
                        f"{format_number(cv['value'])} {cv['unit']} for this family)"
                    )
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
        elif rec.tool == "get_product":
            fam = r.get("family")
            if fam is not None:  # family-envelope result (capability-only family, no product rows)
                caps = r.get("capabilities") or []
                if caps:
                    segs = []
                    for c in caps:
                        seg = (
                            f"capacity up to {format_number(c.get('capacity_max'))} "
                            f"{c.get('capacity_unit')}, discharge up to "
                            f"{format_number(c.get('discharge_p_max'))} {c.get('pressure_unit')}"
                        )
                        if c.get("standards"):
                            seg += f", standards {', '.join(c['standards'])}"
                        if c.get("comp_type"):
                            seg += f", type {c['comp_type']}"
                        if c.get("gases"):
                            seg += f", gases {', '.join(c['gases'])}"
                        segs.append(seg)
                    body = " | ".join(segs)
                else:  # a family that resolved but publishes no envelope figures
                    body = _truncate(fam.get("summary"), 200) or "no published envelope figures"
                lines.append(
                    f"[{rec.tr_id}] get_product family={fam.get('family_id')} "
                    f"{fam.get('family_name')} :: {body}"
                )
                continue
            for p in r.get("products", []):
                seg = p.get("display_name") or p.get("family_name") or p.get("family_id")
                if p.get("variant") and p["variant"].lower() not in (seg or "").lower():
                    seg = f"{seg} ({p['variant']})"  # so the model can name the sibling series
                fam = p.get("family_name")
                summ = p.get("family_summary")
                desc = p.get("description")
                attrs = p.get("attributes")
                extra = "; ".join(
                    x for x in (
                        f"family {fam}" if fam else "",
                        f"summary: {_truncate(summ, 200)}" if summ else "",
                        f"description: {_truncate(desc, 200)}" if desc else "",
                        f"attributes {attrs}" if attrs else "",
                    ) if x
                )
                lines.append(f"[{rec.tr_id}] get_product :: {seg}" + (f" — {extra}" if extra else ""))
        elif rec.tool == "list_products":
            prods = "; ".join(
                f"{f.get('family_id')}={f.get('name')!r}" for f in r.get("families", [])
            )
            lines.append(f"[{rec.tr_id}] list_products :: {prods}")
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


# Phrases that mean "I couldn't find it" — an absence claim (LLD-AG absence protocol).
_ABSENCE_RE = re.compile(
    r"don'?t have|do not have|not in (our|the) published|can'?t confirm|cannot confirm|"
    r"couldn'?t find|could not find|no (published )?(information|details|record|data)|"
    r"not (available|listed|published) in",
    re.IGNORECASE,
)


def looks_like_absence(text: str) -> bool:
    """True if the drafted answer reads as 'I couldn't find that' (LLD-AG absence protocol)."""
    return bool(_ABSENCE_RE.search(text or ""))


# A model sometimes appends a spurious, EMPTY "Sources:" line to a NON-answer reply (a slot/clarify
# question), echoing the persona's citation talk even though such replies carry no citations — the
# real Sources panel is built from structured `citations` on answer turns only, never from this text.
# Strip a TRAILING sources declaration ONLY when it lists nothing; a line naming real sources is left
# untouched (conservative — never silently drop named provenance).
_EMPTY_SOURCES_RE = re.compile(
    r"(?:\r?\n)*[ \t]*sources?[ \t]*:[ \t]*"
    r"(?:\[[ \t]*\]|\([ \t]*none[ \t]*\)|none|n/?a|—|-|\.)?[ \t]*$",
    re.IGNORECASE,
)


def strip_empty_sources_trailer(text: str) -> str:
    """Remove a trailing, EMPTY 'Sources:'/'Source:' line (and any preceding blank lines) from a
    NON-answer reply. Leaves a 'Sources:' line that lists real content untouched. Not for the answer
    path — the structured Sources panel is unaffected (it never comes from message text)."""
    if not text:
        return text
    return _EMPTY_SOURCES_RE.sub("", text).rstrip()


def compose_grounded_answer(
    *,
    complete: CompleteFn,
    prompt_body: str,
    history: list[dict],
    latest_user: str,
    records: list[ToolCallRecord],
    language: str,
    extra_instruction: str = "",
    search_fallback=None,
) -> tuple[str, list[str]]:
    """Ask the runtime LLM to write the answer from the tool results, citing their tr ids.

    Returns ``(message, cited_tr_ids)``. The grounding gate (LLD-RT-05) validates the result
    afterwards; this only drafts it. Numbers must be quoted from the tool block, never invented.

    **Absence protocol (LLD-AG):** an "I couldn't find that" answer is only honest once retrieval
    has actually looked. If the draft reads as an absence claim and no ``search_documents`` ran
    this turn, ``search_fallback()`` is invoked (it must append a search to ``records``) and the
    answer is recomposed once over the enlarged evidence — then it either answers from the hit or
    genuinely declares absence.
    """

    def _draft() -> tuple[str, list[str]]:
        system = (
            prompt_body
            + "\n\nUse ONLY the tool results below. Quote figures exactly as they appear; never "
            "invent a number, name or date. Put the tool_result ids (trN) you relied on in "
            "`citations`.\n\nTool results:\n" + render_tool_context(records)
        )
        if extra_instruction:
            system += "\n\n" + extra_instruction
        # Response-language instruction LAST, emphatic and current-turn-scoped, so it wins over
        # the language of earlier turns in the history (LLD-RT-07).
        system += (
            f"\n\n{respond_in(language)} Reply in this language regardless of the language used "
            "in earlier messages of this conversation."
        )
        messages = build_chat_messages(system, history, latest_user)
        # Signal the reply language to the runtime thinking-gate for THIS compose call: the
        # LLM_DISABLE_THINKING knob silences en/hinglish compose but leaves hi thinking on, since the
        # Devanagari register needs compose reasoning (compose_thinking_off, LLD-RT-07). Attribute
        # idiom (like last_tokens); the runtime seam reads it, other complete seams ignore it.
        try:
            complete.compose_language = language
        except (AttributeError, TypeError):  # a seam that can't carry attributes ⇒ no-op
            pass
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

    message, citations = _draft()
    searched = any(r.tool == "search_documents" for r in records)
    if search_fallback is not None and looks_like_absence(message) and not searched:
        try:
            search_fallback()  # appends a search_documents record to `records`
        except Exception:  # noqa: BLE001 - retrieval infra missing ≠ a crash; keep the draft
            return message, citations
        message, citations = _draft()  # recompose over the enlarged evidence
    return message, citations
