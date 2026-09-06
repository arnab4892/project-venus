"""After-Sales Intake agent (LLD-AG-04).

Collects the facts a service handoff needs — model, serial/year, site city, need, contact
preference — **one question per turn** (slot-filling, like application_discovery). It performs
**no diagnosis whatsoever**: it never suggests a cause or fix, only gathers details and hands
off. Tools: ``get_product`` (03), ``get_office`` (06).

On completion the action is ``handoff`` with ``lead_type=after_sales``; ``get_office`` resolves
the branch office for the site city and the close **names that office**. This is a **stub close
in 5b — no ``ops.lead`` row is written** (lead capture + email are milestone 6).
"""

from __future__ import annotations

from agentkit.extract.llm import call_json
from agentkit.runtime.agents.base import AgentOutput, strip_empty_sources_trailer
from agentkit.runtime.language import respond_in
from agentkit.runtime.ops import MessageRecord
from agentkit.runtime.schemas import AFTER_SALES_SCHEMA, AFTER_SALES_SLOTS
from agentkit.runtime.triage import build_chat_messages

_SLOT_QUESTIONS = {
    "model": "Which Jyotech machine is it — the model name or number?",
    "serial_or_year": "What's the serial number, or roughly the year it was supplied?",
    "site_city": "Which city is the machine installed in?",
    "need": "What do you need — a service visit, spares, or something else?",
    "contact_pref": "How would you prefer we reach you — a call, WhatsApp or email?",
    "contact_detail": "Great — and the best number or email address to reach you on?",
}


def _contact_question(slots: dict) -> str:
    """The contact-detail question, phrased for the preference the visitor chose."""
    pref = (slots.get("contact_pref") or "").lower()
    if "email" in pref:
        return "Perfect — what's the best email address to reach you on?"
    if "whatsapp" in pref or "call" in pref or "phone" in pref:
        return "Perfect — what's the best phone number to reach you on?"
    return _SLOT_QUESTIONS["contact_detail"]


def _extract_slots(raw: dict) -> dict:
    return {k: raw.get(k) for k in AFTER_SALES_SLOTS if raw.get(k) is not None}


def _first_missing(slots: dict) -> str | None:
    for slot in AFTER_SALES_SLOTS:
        if slots.get(slot) is None:
            return slot
    return None


def run(ctx, *, prompt_body, triage, history, latest_user, tools) -> AgentOutput:
    language = triage.get("language", "en")
    messages = build_chat_messages(
        prompt_body + "\n\n" + respond_in(language), history, latest_user
    )
    raw = call_json(
        client=None,
        complete=ctx.complete,
        messages=messages,
        json_schema=AFTER_SALES_SCHEMA,
        schema_name="after_sales_slots",
        kind="after_sales_slots",
        section_id=None,
    )
    slots = _extract_slots(raw)

    missing = _first_missing(slots)
    if missing is not None:
        if missing == "contact_detail":
            # Ask for the actual detail deterministically — the slot LLM sometimes writes a
            # closing line ("All set…") here instead of requesting the email/phone.
            question = _contact_question(slots)
        else:
            question = strip_empty_sources_trailer(raw.get("message") or "") or _SLOT_QUESTIONS[missing]
        return AgentOutput(
            action="ask_slot",
            messages=[MessageRecord("assistant", "text", question)],
            slots=slots,
            output={**raw, "asked_slot": missing},
        )

    # All details collected (incl. the actual contact detail) → name the region's office and hand
    # off (stub — no ops.lead in 5b; the intake output now carries everything M6 needs).
    office_rec = tools.get_office(city=slots["site_city"])
    office = office_rec.result.get("office") or {}
    office_city = office.get("city") or slots["site_city"]
    office_email = office.get("email")
    contact = f" ({office_email})" if office_email else ""
    msg = (
        f"Thanks — I've noted your {slots['model']} in {slots['site_city']} and that you need "
        f"{slots['need']}. Our {office_city} office{contact} looks after your region; I'll pass "
        f"your details to them and they'll reach you on {slots['contact_detail']}."
    )
    return AgentOutput(
        action="handoff",
        messages=[MessageRecord("assistant", "text", msg)],
        slots=slots,
        output={
            "action": "handoff",
            "lead_type": "after_sales",
            "office_id": office.get("office_id"),
            # Everything M6 needs to write the ops.lead row (M6 must format-check contact_detail
            # and apply the PRD consent gate before storing it).
            "intake": {k: slots.get(k) for k in AFTER_SALES_SLOTS},
        },
    )
