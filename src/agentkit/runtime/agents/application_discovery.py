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

import json
import logging
import re

from sqlalchemy import text

from agentkit.extract.llm import call_json
from agentkit.runtime.agents.base import (
    AgentOutput,
    compose_grounded_answer,
    strip_empty_sources_trailer,
)
from agentkit.runtime.format import format_number
from agentkit.runtime.grounding import spec_numbers
from agentkit.runtime.language import respond_in
from agentkit.runtime.ops import MessageRecord
from agentkit.runtime.schemas import APPLICATION_SCHEMA
from agentkit.runtime.triage import build_chat_messages

logger = logging.getLogger(__name__)

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


# Token guards for the OPTIONAL filter slots (LLD-AG-01, Fix 1b). An inferred `lubricated` /
# `standard` is kept only when the visitor actually stated a matching token — a prime-mover phrase
# like "gas engine driven" is NOT a lubrication statement, and keeping the invented `lubricated=false`
# silently excludes a Lubricated family (the turn 8fdc353f miss). The relaxation retry only catches an
# EMPTY match, not the wrong-but-nonempty match a false filter produces, so this pre-match guard is
# the fix.
_LUBRICATION_RE = re.compile(
    # Latin (en + hinglish): oil-free / oilless / non-lube / lube / the "lubric" stem — never bare
    # "oil" (so "oilfield gas" must not validate a filter). Plus Devanagari (hi) forms.
    r"oil[\s-]?free|oil[\s-]?less|non[\s-]?lube|lube|lubric"
    r"|ऑयल[\s-]?फ्री|ऑइल[\s-]?फ्री|लुब्रिकेटेड|नॉन[\s-]?लुब्रिकेटेड|तेल[\s-]?मुक्त",
    re.IGNORECASE,
)
# Standard codes are Latin in every script (register rule). The acronyms are never ordinary words in
# any capitalisation → case-insensitive. The ambiguous short codes need an adjacent digit AND
# uppercase, else ordinary English validates a hallucinated filter ("capacity is 3000" → is+digit).
_STANDARD_ACRONYM_RE = re.compile(
    r"\b(?:api|iso|asme|atex|ped|nfpa|eiga|din|ansi|iec)\b", re.IGNORECASE
)
_STANDARD_SHORT_RE = re.compile(r"\b(?:EN|IS|BS|BIS)[\s-]?\d")  # case-sensitive UPPERCASE


def _message_states_lubrication(text: str) -> bool:
    """True if the text explicitly states a lubrication preference (any supported script)."""
    return bool(_LUBRICATION_RE.search(text or ""))


def _message_states_standard(text: str) -> bool:
    """True if the text names a standard/certification code (Latin, per the register rules)."""
    t = text or ""
    return bool(_STANDARD_ACRONYM_RE.search(t) or _STANDARD_SHORT_RE.search(t))


def _guard_inferred_filters(slots: dict, latest_user: str, history: list[dict]) -> dict:
    """Null an over-inferred `lubricated` / `standard` slot the visitor never actually stated.

    Scans the current message PLUS prior USER turns (a preference stated earlier must survive);
    assistant turns are deliberately not scanned — echoing the bot's own offer back would validate
    nearly any hallucinated filter. Returns the discarded {slot: value} for tracing. Mutates `slots`.
    """
    convo = " ".join(
        [latest_user or ""] + [m.get("text") or "" for m in history if m.get("role") == "user"]
    )
    stripped: dict = {}
    if slots.get("lubricated") is not None and not _message_states_lubrication(convo):
        stripped["lubricated"] = slots["lubricated"]
        slots["lubricated"] = None
    if slots.get("standard") is not None and not _message_states_standard(convo):
        stripped["standard"] = slots["standard"]
        slots["standard"] = None
    for slot, value in stripped.items():
        # Observability: every strip is traced (which slot, the discarded value) so the accepted
        # user-turns-only limitation's real-world frequency is measurable; logging is fail-open.
        logger.info("slot-guard: stripped %s=%r (no %s token in visitor turns)", slot, value, slot)
    return stripped


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


_REQUIRED = ("gas", "capacity", "capacity_unit", "discharge_p")
_DUTY_KEYS = ("gas", "capacity", "capacity_unit", "discharge_p", "lubricated", "standard")


def _prior_duty(conn, session_id) -> dict | None:
    """The authoritative prior duty for follow-up carry-forward: the args of the most-recent
    `match_capability` call in THIS session (from the recorded tool calls). Within that turn, take
    the first non-null per key so the stated duty wins over a later relaxation call (which nulls the
    optional filters). Returns None outside a session or before any match this session.
    """
    if session_id is None or conn is None:
        return None
    # Fail-open, like every other bolt-on read (sources resolver, tracing): the carry-forward read
    # must never break the turn. The SELECT runs inside a SAVEPOINT so a DB error rolls back only
    # this read and cannot poison the turn's transaction (match_capability shares the connection);
    # any exception — DB, or a malformed args row — logs and returns None (no inheritance this turn).
    try:
        with conn.begin_nested():
            rows = conn.execute(
                text(
                    "SELECT t.seq, tc.args FROM ops.tool_call tc "
                    "JOIN ops.agent_invocation ai ON ai.inv_id = tc.inv_id "
                    "JOIN ops.turn t ON t.turn_id = ai.turn_id "
                    "WHERE t.session_id = :sid AND tc.tool = 'match_capability' "
                    "ORDER BY t.seq DESC"
                ),
                {"sid": session_id},
            ).all()
        if not rows:
            return None
        top_seq = rows[0][0]
        prior: dict = {}
        for seq, args in rows:
            if seq != top_seq:
                break
            a = args if isinstance(args, dict) else json.loads(args)
            for k in _DUTY_KEYS:
                if prior.get(k) is None and a.get(k) is not None:
                    prior[k] = a[k]
        return prior or None
    except Exception:  # noqa: BLE001 - fail-open: a carry-forward read failure never breaks the turn
        logger.warning(
            "carry-forward: prior-duty read failed; proceeding without inheritance", exc_info=True
        )
        return None


def _merge_followup(slots: dict, prior: dict, latest_user: str) -> dict:
    """Merge the newly extracted slots over the prior duty (LLD-AG-01 carry-forward).

    A **full restatement** (all required slots stated anew) overrides everything and inherits
    nothing — including the optional filters, so a stale oil-free/standard from an earlier duty does
    not silently ride onto a fresh one. A **partial** follow-up inherits the missing slots (required
    and optional) from the prior duty. Ambiguity is asked, never guessed (Fix 1a): when the visitor
    states a NEW flow but **the current message carries no flow-with-unit** ("what about 80000?"),
    the unit is left null so the missing-slot check produces one clarifying question — regardless of
    a unit the slot LLM may have inferred from context (it has no face value to take).
    """
    if all(slots.get(k) is not None for k in _REQUIRED):
        merged = dict(slots)  # full restatement — nothing inherited
    else:
        merged = {k: prior.get(k) for k in _DUTY_KEYS if prior.get(k) is not None}
        merged.update({k: v for k, v in slots.items() if v is not None})
    # Ambiguity (Fix 1a), applied whether the LLM under- or over-extracted: it produced a NEW
    # capacity this turn, but the current message carries no flow-with-unit → the unit was assumed
    # (from context or inheritance), which has no face value. Null it so the visitor is asked once.
    if slots.get("capacity") is not None and not _CAP_RE.search(latest_user or ""):
        merged["capacity_unit"] = None
    return merged


# Deterministic honest fallback when the compose refuses to name the matched family even after a
# corrective recompose (Fix 3; e.g. a thinking-off "no results" denial despite a real match). Per
# language (LLD-RT-07); the family display name is the only Latin in the hi/hinglish variants (bold
# exact-name exception). Names the family + offers engineers — honest, we DID match.
_FAMILY_BACKSTOP_TEXTS = {
    "en": (
        "Our published **{family}** range is the fit for this duty — let me have our engineers "
        "confirm the exact frame for you. Shall I connect you?"
    ),
    "hi": (
        "इस ड्यूटी के लिए हमारी प्रकाशित **{family}** श्रेणी उपयुक्त बैठती है — मैं अपने इंजीनियरों से सटीक फ़्रेम "
        "की पुष्टि करवा देता हूँ। क्या मैं आपको जोड़ूँ?"
    ),
    "hinglish": (
        "Is duty ke liye hamari published **{family}** range sahi fit hai — main apne engineers se "
        "exact frame confirm karwa deta hoon. Kya main aapko connect karun?"
    ),
}


# The compose sometimes — especially thinking-off — DENIES having any result despite a real match
# (turn 74d45612: "I'm not getting any results from our search … problem with the search tool …
# aren't any documents that match"). That answer slips the numeric/citation gate (the match is
# force-cited, it carries no bad numbers), so it must be caught here. A denial detector targets this
# precisely without false-firing on a legitimate paraphrase of the family name — unlike an
# exact-display-name check, which fires on "process gas reciprocating range" and on any wording the
# LLM chooses — and without intercepting a numeric fabrication (which the grounding gate already
# strips to the fallback).
_ANSWER_DENIES_MATCH_RE = re.compile(
    r"not getting any results|no results|no (matching )?(documents?|records?|data|information)|"
    r"problem with the (search )?tool|search (tool |)(isn'?t|is not|failed|failing|not working)|"
    r"couldn'?t find|could not find|don'?t have (any|that|it)|no such|"
    r"aren'?t any (documents?|results?|matches)|try (again|rephrasing|a different)",
    re.IGNORECASE,
)


def _answer_denies_match(message: str) -> bool:
    """True if the drafted answer reads as a 'no results / tool failed' denial (which must never
    ship when match_capability actually returned a family)."""
    return bool(_ANSWER_DENIES_MATCH_RE.search(message or ""))


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

    # Follow-up carry-forward (LLD-AG-01): before deciding a required slot is missing, merge the
    # newly extracted slots over this session's PRIOR duty (the most recent match_capability args).
    # A partial follow-up ("What about 80000 SCMD?" / "and what about oxygen?") inherits the rest; a
    # full restatement overrides everything; a unitless new flow is asked, never guessed. Scoped to
    # this session's own match_capability calls; the token guards below still run on the merged slots.
    prior = _prior_duty(getattr(tools, "conn", None), getattr(tools, "session_id", None))
    if prior:
        slots = _merge_followup(slots, prior, latest_user)

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
        # A slot question never carries figures. If the LLM's message contains a spec-number (an
        # answer-in-disguise the numeric guard would redact to a mutilated "up to … and …"), DISCARD
        # it and send the deterministic template (Fix 2). _SLOT_QUESTIONS is English-only — a
        # recorded residual (hi/hinglish slot templates flagged in the notes).
        # Also strip a spurious empty "Sources: []" trailer the slot model sometimes echoes from the
        # persona's provenance text — a clarifying question carries no citations (prompt-hygiene net).
        llm_msg = strip_empty_sources_trailer(raw.get("message") or "")
        question = llm_msg if (llm_msg and not spec_numbers(llm_msg)) else _SLOT_QUESTIONS[missing]
        return AgentOutput(
            action="ask_slot",
            messages=[MessageRecord("assistant", "text", question)],
            slots=slots,
            output={**raw, "asked_slot": missing},
        )

    # Guard the OPTIONAL filter slots BEFORE matching: strip an inferred lubricated/standard the
    # visitor never stated, so a prime-mover phrase ("gas engine driven") can't inject a false filter.
    stripped_slots = _guard_inferred_filters(slots, latest_user, history)

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
                **({"stripped_slots": stripped_slots} if stripped_slots else {}),
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

    # Restate the exact (merged) duty so any value carried over from an earlier turn is visible and
    # correctable (Fix 1b). The duty figures are the visitor's own flow + the tool-args pressure, so
    # the numeric guard permits them.
    duty_desc = (
        f"{format_number(slots['capacity'])} {slots['capacity_unit']} of {slots['gas']} at "
        f"{format_number(slots['discharge_p'])} bar"
        + (", oil-free" if slots.get("lubricated") is False else "")
    )
    extra = (
        f"Open by restating the exact duty you are matching against — {duty_desc} — so any value "
        "carried over from an earlier turn in this conversation is visible to the customer and "
        f"correctable in one turn. Then recommend the {top['family_name']} range and state its "
        f"published capacity and pressure exactly — up to {format_number(top.get('capacity_max'))} "
        f"{top.get('capacity_unit')} and up to {format_number(top.get('discharge_p_max'))} "
        f"{top.get('pressure_unit')} — in natural prose, not as a quotation. Do not quote a capacity "
        "or pressure from any other family or from the document text."
    )
    if top.get("near_edge"):
        extra += (
            " This duty sits near the top of that range, so suggest confirming the exact frame with "
            "our engineers."
        )
    else:
        extra += " This duty sits comfortably inside the range, so say so plainly."

    def _compose(instruction: str):
        return compose_grounded_answer(
            complete=ctx.complete,
            prompt_body=prompt_body,
            history=history,
            latest_user=latest_user,
            records=tools.records,
            language=language,
            extra_instruction=instruction,
        )

    message, citations = _compose(extra)
    # Answer-must-name-family backstop (Fix 3): a "no results / tool failed" DENIAL composed despite
    # a real match (turn 74d45612) slips the gate (the match is force-cited, no bad numbers).
    # Recompose once forcing a real answer; if it still denies the match, ship the honest fallback
    # (names the family + offers engineers) rather than the tool-denial.
    if _answer_denies_match(message):
        message, citations = _compose(
            extra
            + f"\n\nIMPORTANT: a matching family WAS found — recommend the {top['family_name']} "
            "range from the match above. Do NOT say there are no results, that the search/tool "
            "failed, or ask the visitor to rephrase; answer from the match."
        )
        if _answer_denies_match(message):
            logger.info(
                "answer-backstop: compose kept denying the match for %s; honest fallback",
                top["family_id"],
            )
            lang = language if language in _FAMILY_BACKSTOP_TEXTS else "en"
            return AgentOutput(
                action="handoff",
                messages=[MessageRecord(
                    "assistant", "text",
                    _FAMILY_BACKSTOP_TEXTS[lang].format(family=top["family_name"]),
                )],
                slots=slots,
                output={
                    "action": "handoff", "reason": "compose_denied_match",
                    "matched_family_id": top["family_id"],
                },
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
            **({"stripped_slots": stripped_slots} if stripped_slots else {}),
        },
    )
