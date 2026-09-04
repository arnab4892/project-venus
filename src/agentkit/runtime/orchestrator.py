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

import logging
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
from agentkit.runtime.sources import resolve_sources
from agentkit.runtime.tools_registry import ToolRunner
from agentkit.runtime.tracing import annotate_trace, observe, trace_session
from agentkit.runtime.triage import needs_clarification, run_triage

logger = logging.getLogger(__name__)

# Fixed, per-language clarify / degrade sentences (LLD-RT-07), selected by the turn's detected
# language. Deterministic and pre-vetted — never LLM-translated (these ship precisely when the
# classifier is unsure or a model call has just failed). Technical terms and units stay English.
_CLARIFY_TEXTS = {
    "en": (
        "Could you tell me a bit more about what you need — a compressor for a specific gas/duty, "
        "product details, company or document information, or service and spares?"
    ),
    "hi": (
        "क्या आप थोड़ा और बता सकते हैं कि आपको क्या चाहिए — किसी ख़ास गैस/ड्यूटी के लिए कंप्रेसर, किसी "
        "उत्पाद की जानकारी, कंपनी या दस्तावेज़ की जानकारी, या सर्विस और स्पेयर पार्ट्स?"
    ),
    "hinglish": (
        "Kya aap thoda aur bata sakte hain ki aapko kya chahiye — kisi specific gas/duty ke liye "
        "compressor, product details, company ya document information, ya service aur spares?"
    ),
}
# Shown when the runtime LLM returns invalid JSON 3× (ExtractionSkipped): degrade gracefully
# to a clarify, never crash the turn. A self-hosted model can occasionally return junk.
_HICCUP_TEXTS = {
    "en": (
        "Sorry — I didn't quite catch that. Could you rephrase, or tell me the gas, flow and "
        "discharge pressure you need?"
    ),
    "hi": (
        "माफ़ कीजिए — मैं ठीक से समझ नहीं पाया। क्या आप दोबारा बता सकते हैं, या जो गैस, फ़्लो और "
        "डिस्चार्ज प्रेशर चाहिए वो बता दें?"
    ),
    "hinglish": (
        "Maaf kijiye — main theek se samajh nahi paya. Kya aap dobara bata sakte hain, ya jo gas, "
        "flow aur discharge pressure chahiye woh bata dein?"
    ),
}
# Back-compat aliases: the English strings are the historical single-string constants.
CLARIFY_MESSAGE = _CLARIFY_TEXTS["en"]
LLM_HICCUP_MESSAGE = _HICCUP_TEXTS["en"]


def _lang_of(triage: dict | None) -> str:
    lang = (triage or {}).get("language", "en")
    return lang if lang in _CLARIFY_TEXTS else "en"

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
    # Optional read-only progress seam: called once at the top of each node with a short human
    # label. Live streaming surfaces (the Gradio harness) plug a queue in here; the CLI leaves it
    # None, so the callback is inert and the turn is byte-for-byte unchanged.
    on_stage: Callable[[str], None] | None = None


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

def _stage(wf: "WorkflowState", label: str) -> None:
    """Emit a short human progress label to the optional read-only ``on_stage`` seam."""
    if wf.ctx.on_stage is not None:
        wf.ctx.on_stage(label)


@observe(name="n_triage")
def n_triage(state: _GState) -> dict:
    wf = state["wf"]
    _stage(wf, "Understanding your question")
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


@observe(name="n_route")
def n_route(state: _GState) -> dict:
    wf = state["wf"]
    _stage(wf, "Finding the right specialist")
    wf.route, wf.forced_outcome = decide_route(wf.triage)
    return {}


@observe(name="n_agent")
def n_agent(state: _GState) -> dict:
    wf = state["wf"]
    _stage(wf, "Looking into it")
    if wf.route == "clarify":
        wf.reply_messages = [MessageRecord("assistant", "text", _CLARIFY_TEXTS[_lang_of(wf.triage)])]
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
        wf.reply_messages = [MessageRecord("assistant", "text", _HICCUP_TEXTS[_lang_of(wf.triage)])]
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


@observe(name="n_ground")
def n_ground(state: _GState) -> dict:
    wf = state["wf"]
    _stage(wf, "Checking our sources")
    out = wf.agent_out

    if out is not None:
        if out.action == "answer":
            gr = ground_answer(
                out.draft_text,
                out.citations,
                wf.tool_records,
                _user_texts(wf.history, wf.latest_user),
                language=(wf.triage or {}).get("language", "en"),
            )
            wf.reply_messages = [MessageRecord("assistant", "text", gr.text)]
            # Structured extras (e.g. a document_card) ride along only when grounding passed —
            # a failed answer ships the fallback text alone (LLD-AG-03).
            if gr.ok and out.extra_messages:
                wf.reply_messages.extend(out.extra_messages)
            wf.citations = gr.citations
            wf.grounding = gr.grounding
            # A draft that fails the grounding gate is stripped and the fallback sentence ships
            # (gr.ok False, gr.text == FALLBACK_TEXT). Record that honestly as `fallback`, not
            # `answered`, so gate-stripped turns are countable in ops monitoring (LLD-RT-05).
            wf.outcome = "answered" if gr.ok else "fallback"
            # Persist what the customer saw: attach the resolved customer-facing sources to the
            # answer message payload (ops.message.payload — no schema change). Fail-open: the
            # resolver only reads facts.* views, but a lookup failure must never break the turn —
            # on any error, log and ship the answer with no sources.
            if wf.citations:
                try:
                    resolved = resolve_sources(wf.ctx.conn, wf.citations)
                except Exception:  # noqa: BLE001 - sources are presentational; never fail a turn
                    logger.warning("resolve_sources failed; answering without sources", exc_info=True)
                    resolved = None
                if resolved:
                    answer_msg = wf.reply_messages[0]
                    answer_msg.payload = {**(answer_msg.payload or {}), "sources": resolved}
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


@observe(name="n_respond")
def n_respond(state: _GState) -> dict:
    wf = state["wf"]
    _stage(wf, "Finishing up")
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


@observe(name="run_turn")
def run_turn(ctx: Ctx, session_id, latest_user: str, *, history: list[dict] | None = None) -> RunResult:
    """Run one full turn through the graph and persist it. Returns a display/trace result.

    ``@observe`` makes this the trace **root**, so the per-node/tool/LLM spans created beneath it
    nest into exactly one Langfuse trace per turn (dev-only; inert when tracing is off). session_id
    rides the trace via ``trace_session``; turn_id + active prompt_version ids are attached as
    metadata once the turn has run (turn_id is only minted at ``respond``). ``ops.*`` stays the
    system of record.
    """
    wf = WorkflowState(ctx=ctx, session_id=session_id, latest_user=latest_user, history=history or [])
    with trace_session(session_id):
        _graph().invoke({"wf": wf})
        annotate_trace(
            turn_id=wf.turn_id,
            session_id=session_id,
            prompt_ids=[inv.prompt_id for inv in wf.invocations if inv.prompt_id],
        )

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
