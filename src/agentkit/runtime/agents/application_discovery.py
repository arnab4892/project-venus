"""Application Discovery agent (LLD-AG-01).

Guided slot-filling for an industrial compressor duty. Slot order gas → capacity(+unit) →
discharge_p; it asks **exactly one** missing slot per turn and **never invents a value**. Once
the three required slots are present it calls ``match_capability`` (with the per-client gas
alias map + null-lubricated caveat) and either:

* matches → composes a grounded answer citing the capability row and (when retrieval is
  available) the catalogue chunk; ``near_edge`` adds an "confirm with our engineers" note;
* no match → an honest "not in our published range" + handoff-style close (a **stub** in 5a —
  no ``ops.lead`` row yet; that is milestone 6).

The slot-extraction LLM call and the answer-compose call both go through the ``complete=`` seam.
``search_documents`` is best-effort: when the embedder/vector table is unavailable (e.g. the
demo seed has no chunks) the agent answers from the capability match alone, never crashing.
"""

from __future__ import annotations

from agentkit.extract.llm import call_json
from agentkit.runtime.agents.base import AgentOutput, compose_grounded_answer
from agentkit.runtime.language import respond_in
from agentkit.runtime.ops import MessageRecord
from agentkit.runtime.schemas import APPLICATION_SCHEMA
from agentkit.runtime.triage import build_chat_messages

_SLOT_QUESTIONS = {
    "gas": "Which gas do you need to compress?",
    "capacity": "What flow/capacity do you need, with units (e.g. Nm³/hr, SCMD or kg/hr)?",
    "discharge_p": "What discharge pressure do you need (in bar)?",
}


def _extract_slots(raw: dict) -> dict:
    keys = ["gas", "capacity", "capacity_unit", "discharge_p", "lubricated", "standard",
            "industry", "timeline"]
    return {k: raw.get(k) for k in keys if raw.get(k) is not None}


def _first_missing(slots: dict) -> str | None:
    if slots.get("gas") is None:
        return "gas"
    if slots.get("capacity") is None or slots.get("capacity_unit") is None:
        return "capacity"
    if slots.get("discharge_p") is None:
        return "discharge_p"
    return None


def run(ctx, *, prompt_body, triage, history, latest_user, tools) -> AgentOutput:
    language = triage.get("language", "en")
    messages = build_chat_messages(prompt_body + "\n\n" + respond_in(language), history, latest_user)
    raw = call_json(
        client=None,
        complete=ctx.complete,
        messages=messages,
        json_schema=APPLICATION_SCHEMA,
        schema_name="application_slots",
        kind="slots",
        section_id=None,
    )
    slots = _extract_slots(raw)

    missing = _first_missing(slots)
    if missing is not None:
        question = raw.get("message") or _SLOT_QUESTIONS[missing]
        return AgentOutput(
            action="ask_slot",
            messages=[MessageRecord("assistant", "text", question)],
            slots=slots,
            output={**raw, "asked_slot": missing},
        )

    # Required slots present → match the duty against the published envelope.
    match_rec = tools.match_capability(
        gas=slots["gas"],
        capacity=float(slots["capacity"]),
        capacity_unit=slots["capacity_unit"],
        discharge_p=float(slots["discharge_p"]),
        lubricated=slots.get("lubricated"),
        standard=slots.get("standard"),
    )
    result = match_rec.result
    matches = result.get("matches", [])
    non_comparable = result.get("non_comparable_candidates", [])

    if not matches:
        # No published family whose comparable envelope contains the duty. Offer engineer review
        # (a stub in 5a — no ops.lead yet); mention non-comparable relatives in one line only.
        note = (
            " We do make related machines whose published data I can't directly compare to your "
            "duty." if non_comparable else ""
        )
        msg = (
            f"I can't confirm a published compressor range that covers {slots['gas']} at "
            f"{slots['capacity']} {slots['capacity_unit']} and {slots['discharge_p']} bar.{note} "
            "I can connect you with our engineers for a tailored solution — shall I?"
        )
        return AgentOutput(
            action="handoff",
            messages=[MessageRecord("assistant", "text", msg)],
            slots=slots,
            output={
                "action": "handoff", "reason": "no_match",
                "non_comparable": [c.get("family_name") for c in non_comparable],
            },
        )

    # Answer from the TOP match's OWN published fields; retrieve within THAT family only, for
    # supporting prose/standards — a chunk's number never overrides the row's (LLD-AG-01).
    top = matches[0]
    try:
        tools.search_documents(
            f"{top['family_name']} {slots['gas']}",
            division=triage.get("division") if triage.get("division") != "unknown" else None,
            family_ids=[top["family_id"]],
            k=3,
        )
    except Exception:  # noqa: BLE001 - retrieval infra missing ≠ a match failure
        pass

    extra = (
        f"The recommended family is {top['family_name']}. State its published limits from the "
        f"match_capability result — capacity up to {top.get('capacity_max')} "
        f"{top.get('capacity_unit')}, discharge up to {top.get('discharge_p_max')} "
        f"{top.get('pressure_unit')} — as published limits, not a quotation. Do not quote a "
        "capacity or pressure figure from any other family or from the document text."
    )
    if top.get("near_edge"):
        extra += (
            " This duty is near that family's published limit — recommend confirming the exact "
            "frame with Jyotech's engineers."
        )
    else:
        extra += " This duty sits inside the published range, so present it confidently."

    message, citations = compose_grounded_answer(
        complete=ctx.complete,
        prompt_body=prompt_body,
        history=history,
        latest_user=latest_user,
        records=tools.records,
        language=language,
        extra_instruction=extra,
    )
    return AgentOutput(
        action="answer",
        draft_text=message,
        citations=citations,
        slots=slots,
        output={
            "action": "answer",
            "matched_family_id": top["family_id"],
            "near_edge": top.get("near_edge"),
        },
    )
