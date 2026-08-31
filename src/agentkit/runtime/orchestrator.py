"""Orchestrator (LLD-RT-02/03).

A thin LangGraph ``StateGraph`` sequencing the runtime turn:
``ingest_user → triage → route → agent → ground → respond``. All persistence is explicit
``ops.*`` inserts flushed atomically at ``respond`` (``runtime.ops.persist_turn``) — no
LangGraph checkpointer in 5a (deferred, LLD-RT-02); the ``ops`` rows are the single source of
truth, and ``chat --session <id>`` rebuilds context from them.

Triage runs the runtime LLM (LLD-RT-04); routing sends the turn to one agent
(application_discovery, faq_company, or the deflect/holding path for out-of-scope and the
not-yet-built 5b intents); the grounding gate (LLD-RT-05) guards every ``answer``. Everything
flows through the injectable ``complete=`` / ``embed=`` seams so a turn runs offline in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy import Connection

from agentkit.config import Settings, get_settings
from agentkit.extract.llm import ExtractionSkipped
from agentkit.runtime.agents import (
    after_sales_intake,
    application_discovery,
    commercial_routing,
    deflect,
    documents_compliance,
    faq_company,
    product_advisor,
)
from agentkit.runtime.grounding import (
    ground_answer,
    numeric_universe,
    redact_unsourced_spec_numbers,
)
from agentkit.runtime.ops import (
    CitationRecord,
    InvocationRecord,
    MessageRecord,
    TurnRecord,
    next_turn_seq,
    persist_turn,
)
from agentkit.runtime.prompts import active_prompt
from agentkit.runtime.tools_registry import ToolRunner
from agentkit.runtime.triage import needs_clarification, run_triage

CLARIFY_MESSAGE = (
    "Could you tell me a bit more about what you need — a compressor for a specific gas/duty, "
    "product details, company or document information, or service and spares?"
)
# Shown when the runtime LLM returns invalid JSON 3× (ExtractionSkipped): degrade gracefully
# to a clarify, never crash the turn. A self-hosted model can occasionally return junk.
LLM_HICCUP_MESSAGE = (
    "Sorry — I didn't quite catch that. Could you rephrase, or tell me the gas, flow and "
    "discharge pressure you need?"
)

# A safe triage default when the classifier LLM fails — low confidence routes to clarify.
_TRIAGE_FALLBACK = {
    "division": "unknown",
    "intent": "faq",
    "language": "en",
    "in_scope": True,
    "pii_present": False,
    "confidence": 0.0,
}

_AGENTS: dict[str, Callable] = {
    "application_discovery": application_discovery.run,
    "product_advisor": product_advisor.run,
    "documents_compliance": documents_compliance.run,
    "after_sales_intake": after_sales_intake.run,
    "commercial_routing": commercial_routing.run,
    "faq_company": faq_company.run,
    "deflect": deflect.run,
}


@dataclass
class Ctx:
    """Everything a turn needs: the DB connection, the LLM/embed seams and per-client config."""

    conn: Connection
    client: str
    complete: Callable
    embed: Callable | None = None
    settings: Settings = field(default_factory=get_settings)
    gas_aliases: dict[str, str] = field(default_factory=dict)


@dataclass
class WorkflowState:
    ctx: Ctx
    session_id: object
    latest_user: str
    history: list[dict]
    triage: dict | None = None
    route: str | None = None
    forced_outcome: str | None = None
    agent_out: object | None = None
    tool_records: list = field(default_factory=list)
    invocations: list[InvocationRecord] = field(default_factory=list)
    citations: list[CitationRecord] = field(default_factory=list)
    reply_messages: list[MessageRecord] = field(default_factory=list)
    grounding: dict | None = None
    outcome: str | None = None
    turn_id: object | None = None


class _GState(TypedDict):
    wf: WorkflowState


# Triage intent → agent route (LLD-RT-03/04, LLD-AG-01..06). deflect is out-of-scope only.
_INTENT_ROUTE: dict[str, str] = {
    "application_enquiry": "application_discovery",
    "product_question": "product_advisor",
    "documents": "documents_compliance",
    "after_sales": "after_sales_intake",
    "commercial": "commercial_routing",
    "faq": "faq_company",
}


def decide_route(triage: dict) -> tuple[str, str | None]:
    """Return ``(route, forced_outcome)`` for a triage result (LLD-RT-03/04)."""
    if needs_clarification(triage):
        return "clarify", "clarify"
    intent = triage["intent"]
    if not triage["in_scope"] or intent == "out_of_scope":
        return "deflect", "declined_oos"
    # All six in-scope intents land on their own agent (5b); deflect is only out-of-scope.
    route = _INTENT_ROUTE.get(intent)
    if route is not None:
        return route, None
    return "deflect", "declined_oos"


def _user_texts(history: list[dict], latest_user: str) -> list[str]:
    prior = [m["text"] for m in history if m.get("role") == "user" and m.get("text")]
    return prior + [latest_user]


# --- graph nodes -----------------------------------------------------------

def n_triage(state: _GState) -> dict:
    wf = state["wf"]
    body, pid = active_prompt(wf.ctx.conn, wf.ctx.client, "triage")
    try:
        wf.triage = run_triage(
            complete=wf.ctx.complete,
            prompt_body=body,
            history=wf.history,
            latest_user=wf.latest_user,
        )
        output = wf.triage
    except ExtractionSkipped as exc:
        # classifier returned invalid JSON 3× → safe low-confidence default → clarify.
        wf.triage = dict(_TRIAGE_FALLBACK)
        output = {"error": "triage_llm_invalid", "detail": str(exc)}
    wf.invocations.append(
        InvocationRecord(
            agent="triage",
            prompt_id=pid,
            input={"latest_user": wf.latest_user},
            output=output,
        )
    )
    return {}


def n_route(state: _GState) -> dict:
    wf = state["wf"]
    wf.route, wf.forced_outcome = decide_route(wf.triage)
    return {}


def n_agent(state: _GState) -> dict:
    wf = state["wf"]
    if wf.route == "clarify":
        wf.reply_messages = [MessageRecord("assistant", "text", CLARIFY_MESSAGE)]
        wf.outcome = "clarify"
        return {}

    body, pid = active_prompt(wf.ctx.conn, wf.ctx.client, wf.route)
    tools = ToolRunner(
        wf.ctx.conn,
        wf.ctx.client,
        gas_aliases=wf.ctx.gas_aliases,
        embed=wf.ctx.embed,
        settings=wf.ctx.settings,
    )
    try:
        out = _AGENTS[wf.route](
            wf.ctx,
            prompt_body=body,
            triage=wf.triage,
            history=wf.history,
            latest_user=wf.latest_user,
            tools=tools,
        )
    except ExtractionSkipped as exc:
        # An agent LLM call returned invalid JSON 3× → degrade to a clarify, never crash.
        wf.tool_records = tools.records
        wf.reply_messages = [MessageRecord("assistant", "text", LLM_HICCUP_MESSAGE)]
        wf.outcome = "clarify"
        wf.invocations.append(
            InvocationRecord(
                agent=wf.route,
                prompt_id=pid,
                input={"latest_user": wf.latest_user},
                output={"error": "agent_llm_invalid", "detail": str(exc)},
                tool_calls=tools.records,
            )
        )
        return {}
    wf.agent_out = out
    wf.tool_records = tools.records
    wf.invocations.append(
        InvocationRecord(
            agent=wf.route,
            prompt_id=pid,
            input={"latest_user": wf.latest_user},
            output=out.output,
            slots=out.slots,
            tool_calls=tools.records,
        )
    )
    return {}


def n_ground(state: _GState) -> dict:
    wf = state["wf"]
    out = wf.agent_out

    if out is not None:
        if out.action == "answer":
            gr = ground_answer(
                out.draft_text,
                out.citations,
                wf.tool_records,
                _user_texts(wf.history, wf.latest_user),
            )
            wf.reply_messages = [MessageRecord("assistant", "text", gr.text)]
            # Structured extras (e.g. a document_card) ride along only when grounding passed —
            # a failed answer ships the fallback text alone (LLD-AG-03).
            if gr.ok and out.extra_messages:
                wf.reply_messages.extend(out.extra_messages)
            wf.citations = gr.citations
            wf.grounding = gr.grounding
            wf.outcome = "answered"
        else:
            wf.reply_messages = out.messages
            wf.outcome = {
                "ask_slot": "asked_slot",
                "handoff": "handoff",
                "deflect": wf.forced_outcome or "declined_oos",
            }.get(out.action, "answered")

    # The numeric guard applies to EVERY outgoing message, whatever the action (LLD-RT-05): a
    # spec figure that is neither in this turn's tool results nor in the visitor's own message is
    # a fabrication (e.g. published limits parroted into a slot question from a prompt exemplar).
    # A pure slot question needs no citation, but a slot question carrying such a figure is an
    # answer in disguise and the figure is stripped exactly as it would be from an answer.
    allowed = numeric_universe(wf.tool_records, _user_texts(wf.history, wf.latest_user))
    stripped_all: list[float] = []
    for m in wf.reply_messages:
        if m.kind == "text" and m.text:
            clean, stripped = redact_unsourced_spec_numbers(m.text, allowed)
            if stripped:
                m.text = clean
                stripped_all.extend(stripped)
    if stripped_all:
        wf.grounding = {**(wf.grounding or {}), "stripped_unsourced_numbers": sorted(set(stripped_all))}
    return {}


def n_respond(state: _GState) -> dict:
    wf = state["wf"]
    messages = [MessageRecord("user", "text", wf.latest_user), *wf.reply_messages]
    turn = TurnRecord(
        session_id=wf.session_id,
        seq=next_turn_seq(wf.ctx.conn, wf.session_id),
        triage=wf.triage,
        routed_agent=wf.route,
        grounding=wf.grounding,
        outcome=wf.outcome,
        messages=messages,
        invocations=wf.invocations,
        citations=wf.citations,
    )
    wf.turn_id = persist_turn(wf.ctx.conn, turn)
    return {}


def build_graph():
    """Compile the triage → route → agent → ground → respond graph (in-memory, no checkpointer)."""
    g = StateGraph(_GState)
    g.add_node("triage", n_triage)
    g.add_node("route", n_route)
    g.add_node("agent", n_agent)
    g.add_node("ground", n_ground)
    g.add_node("respond", n_respond)
    g.set_entry_point("triage")
    g.add_edge("triage", "route")
    g.add_edge("route", "agent")
    g.add_edge("agent", "ground")
    g.add_edge("ground", "respond")
    g.add_edge("respond", END)
    return g.compile()


_GRAPH = None


def _graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


@dataclass
class RunResult:
    turn_id: object
    triage: dict
    outcome: str | None
    route: str | None
    grounding: dict | None
    messages: list[dict]  # assistant bubbles for display: [{role, kind, text, payload}]
    citations: list[dict]  # [{kind, ref_id, locator, url}]
    invocations: list[dict]  # for --show-trace
    tool_results: list[dict] = field(default_factory=list)  # each tool's raw result (eval/guard)
    tool_args: list[dict] = field(default_factory=list)  # each tool's args (allowed-number set)


def run_turn(ctx: Ctx, session_id, latest_user: str, *, history: list[dict] | None = None) -> RunResult:
    """Run one full turn through the graph and persist it. Returns a display/trace result."""
    wf = WorkflowState(ctx=ctx, session_id=session_id, latest_user=latest_user, history=history or [])
    _graph().invoke({"wf": wf})

    inv_trace = [
        {
            "agent": inv.agent,
            "prompt_id": inv.prompt_id,
            "tool_calls": [
                {"tool": tc.tool, "args": tc.args, "rows_returned": tc.rows_returned}
                for tc in inv.tool_calls
            ],
        }
        for inv in wf.invocations
    ]
    return RunResult(
        turn_id=wf.turn_id,
        triage=wf.triage,
        outcome=wf.outcome,
        route=wf.route,
        grounding=wf.grounding,
        messages=[
            {"role": m.role, "kind": m.kind, "text": m.text, "payload": m.payload}
            for m in wf.reply_messages
        ],
        citations=[
            {"kind": c.kind, "ref_id": c.ref_id, "locator": c.locator, "url": c.url}
            for c in wf.citations
        ],
        invocations=inv_trace,
        tool_results=[rec.result for rec in wf.tool_records],
        tool_args=[rec.args for rec in wf.tool_records],
    )
