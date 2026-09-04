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

import re

from agentkit.extract.llm import call_json
from agentkit.runtime.agents.base import AgentOutput, compose_grounded_answer
from agentkit.runtime.format import format_number
from agentkit.runtime.language import respond_in
from agentkit.runtime.ops import MessageRecord
from agentkit.runtime.schemas import APPLICATION_SCHEMA
from agentkit.runtime.triage import build_chat_messages

_SLOT_QUESTIONS = {
    "gas": "Which gas do you need to compress?",
    "capacity": "What flow/capacity do you need, with units (e.g. Nm³/hr, SCMD or kg/hr)?",
    "discharge_p": "What discharge pressure do you need (in bar)?",
}

# No-match handoff (stub, 5a) — a fixed, per-language template selected by the turn's detected
# language (LLD-RT-07), mirroring commercial_routing's `_TEMPLATES`. Technical terms, units and the
# visitor's own gas/capacity/pressure values stay English even in the hi/hinglish register.
_NOMATCH_TEMPLATES = {
    "en": (
        "I can't confirm a published compressor range that covers {gas} at "
        "{capacity} {capacity_unit} and {discharge_p} bar.{note} "
        "I can connect you with our engineers for a tailored solution — shall I?"
    ),
    "hi": (
        "मैं ऐसी कोई प्रकाशित कंप्रेसर श्रेणी पक्की नहीं कर पा रहा हूँ जो {gas} को "
        "{capacity} {capacity_unit} और {discharge_p} bar पर कवर करती हो।{note} "
        "मैं आपको हमारे इंजीनियरों से एक अनुकूलित समाधान के लिए जोड़ सकता हूँ — क्या मैं ऐसा करूँ?"
    ),
    "hinglish": (
        "Main koi aisi published compressor range confirm nahi kar pa raha hoon jo {gas} ko "
        "{capacity} {capacity_unit} aur {discharge_p} bar par cover karti ho.{note} "
        "Main aapko hamare engineers se ek tailored solution ke liye jod sakta hoon — kya main aisa karun?"
    ),
}
_NOMATCH_NOTE = {
    "en": " We do make related machines whose published data I can't directly compare to your duty.",
    "hi": " हम कुछ संबंधित मशीनें भी बनाते हैं जिनके प्रकाशित डेटा की सीधी तुलना मैं आपकी ड्यूटी से नहीं कर सकता।",
    "hinglish": (
        " Hum kuch related machines bhi banate hain jinka published data main seedhe aapki duty "
        "se compare nahi kar sakta."
    ),
}

# Regexes to detect that the message ALREADY states a flow-with-units and a pressure — used to
# catch the slot LLM under-extracting a complete duty (structural slot-complete rule, LLD-AG-01).
_CAP_RE = re.compile(
    r"\d[\d,\.]*\s*(?:n\s*m\s*3\s*/?\s*hr?|nm³|scmd|scmh|kg\s*/\s*hr|m3\s*/\s*hr|m³/\s*hr|lpm|cfm|tpd)",
    re.IGNORECASE,
)
_PRES_RE = re.compile(r"\d[\d,\.]*\s*(?:barg|bar|psi)", re.IGNORECASE)

_REEXTRACT_INSTRUCTION = (
    "The visitor's message states a gas, a flow rate WITH UNITS, and a discharge pressure. "
    "Extract ALL of them now — do NOT leave gas, capacity, capacity_unit or discharge_p null when "
    "the message contains them, and do NOT ask for something the message already gave."
)


def _message_has_full_duty(text: str) -> bool:
    """True if the message already states a flow-with-units AND a pressure (a complete duty)."""
    return bool(_CAP_RE.search(text or "") and _PRES_RE.search(text or ""))


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

    # Structural slot-complete rule (LLD-AG-01): if the message plainly states a flow-with-units
    # and a pressure but the slot LLM under-extracted a numeric slot, re-extract once emphatically
    # — a complete duty MUST reach match_capability, not a slot question.
    if _first_missing(slots) in ("capacity", "discharge_p") and _message_has_full_duty(latest_user):
        retry = call_json(
            client=None,
            complete=ctx.complete,
            messages=build_chat_messages(
                prompt_body + "\n\n" + respond_in(language) + "\n\n" + _REEXTRACT_INSTRUCTION,
                history, latest_user,
            ),
            json_schema=APPLICATION_SCHEMA,
            schema_name="application_slots",
            kind="slots_retry",
            section_id=None,
        )
        retried = _extract_slots(retry)
        if _first_missing(retried) is None:  # only accept a retry that completed the duty
            slots, raw = retried, retry

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

    # Relaxation: `standard` and `lubricated` are OPTIONAL filters, and the slot LLM sometimes
    # infers one the visitor never stated (e.g. "diaphragm compressor" → API-618). A published
    # family may not list that standard/lubrication yet still fit the duty, so if the filtered
    # match is empty we retry on the required dimensions (gas + capacity + pressure) alone before
    # concluding no-match. Capacity/pressure envelope filtering is unchanged, so this never
    # invents an out-of-range match — the family's own row stays the authority.
    if not matches and (slots.get("standard") or slots.get("lubricated") is not None):
        match_rec = tools.match_capability(
            gas=slots["gas"],
            capacity=float(slots["capacity"]),
            capacity_unit=slots["capacity_unit"],
            discharge_p=float(slots["discharge_p"]),
            lubricated=None,
            standard=None,
        )
        result = match_rec.result
        matches = result.get("matches", [])
        non_comparable = result.get("non_comparable_candidates", [])

    if not matches:
        # No published family whose comparable envelope contains the duty. Offer engineer review
        # (a stub in 5a — no ops.lead yet); mention non-comparable relatives in one line only.
        lang = language if language in _NOMATCH_TEMPLATES else "en"
        note = _NOMATCH_NOTE[lang] if non_comparable else ""
        msg = _NOMATCH_TEMPLATES[lang].format(
            gas=slots["gas"],
            capacity=slots["capacity"],
            capacity_unit=slots["capacity_unit"],
            discharge_p=slots["discharge_p"],
            note=note,
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
        f"Name the gas the visitor asked about ({slots['gas']}) in your reply, then "
        f"recommend the {top['family_name']} range. State its published capacity and pressure "
        f"exactly — up to {format_number(top.get('capacity_max'))} {top.get('capacity_unit')} and "
        f"up to {format_number(top.get('discharge_p_max'))} {top.get('pressure_unit')} — in natural "
        "prose, not as a quotation. Do not quote a capacity or pressure from any other family or "
        "from the document text."
    )
    if top.get("near_edge"):
        extra += (
            " This duty sits near the top of that range, so suggest confirming the exact frame with "
            "our engineers."
        )
    else:
        extra += " This duty sits comfortably inside the range, so say so plainly."

    message, citations = compose_grounded_answer(
        complete=ctx.complete,
        prompt_body=prompt_body,
        history=history,
        latest_user=latest_user,
        records=tools.records,
        language=language,
        extra_instruction=extra,
    )
    # The matched row is the authority for the recommendation, so always ground it — cite the
    # match even if the compose LLM only listed the supporting catalogue chunk (honest: the match
    # IS what the answer is built on). Its cap + family ids flow through the grounding gate.
    if match_rec.tr_id not in citations:
        citations.append(match_rec.tr_id)
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
