"""JSON schemas for the runtime LLM's structured outputs (LLD-RT-04, LLD-AG).

Each is a strict ``json_schema`` passed as ``response_format`` (via
:func:`agentkit.extract.llm.build_request_kwargs`) so the model must return well-formed JSON;
the ``complete=`` seam lets tests supply canned JSON without a live endpoint. Schemas are kept
FLAT (no deep nesting) so a self-hosted model's structured-output support is not over-taxed.
"""

from __future__ import annotations

DIVISIONS = ["industrial", "fire_rescue", "diving", "unknown"]
INTENTS = [
    "application_enquiry",
    "product_question",
    "documents",
    "after_sales",
    "commercial",
    "faq",
    "out_of_scope",
]
LANGUAGES = ["en", "hi", "hinglish"]

# LLD-RT-04 triage output.
TRIAGE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "division": {"type": "string", "enum": DIVISIONS},
        "intent": {"type": "string", "enum": INTENTS},
        "language": {"type": "string", "enum": LANGUAGES},
        "in_scope": {"type": "boolean"},
        "pii_present": {"type": "boolean"},
        "confidence": {"type": "number"},
    },
    "required": ["division", "intent", "language", "in_scope", "pii_present", "confidence"],
}

# Application-discovery slot extraction (LLD-AG-01). Flat: values are echoed only when the
# visitor stated them; a missing slot is null. `asked_slot` names the one slot to ask for.
_NUM_OR_NULL = {"type": ["number", "null"]}
_STR_OR_NULL = {"type": ["string", "null"]}
_BOOL_OR_NULL = {"type": ["boolean", "null"]}

APPLICATION_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "gas": _STR_OR_NULL,
        "capacity": _NUM_OR_NULL,
        "capacity_unit": _STR_OR_NULL,
        "discharge_p": _NUM_OR_NULL,
        "lubricated": _BOOL_OR_NULL,
        "standard": _STR_OR_NULL,
        "industry": _STR_OR_NULL,
        "timeline": _STR_OR_NULL,
        "asked_slot": _STR_OR_NULL,
        "message": {"type": "string"},
    },
    "required": [
        "gas", "capacity", "capacity_unit", "discharge_p", "lubricated",
        "standard", "industry", "timeline", "asked_slot", "message",
    ],
}

# FAQ / company answer (LLD-AG-06/03-lite): a grounded message + the tool_result ids used.
FAQ_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "message": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["message", "citations"],
}

# Deflection / holding reply (LLD-AG deflect path).
DEFLECT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"message": {"type": "string"}},
    "required": ["message"],
}

# After-sales intake slots (LLD-AG-04). Flat, echo-only like APPLICATION_SCHEMA: a slot the
# visitor has not yet stated is null; `asked_slot` names the single slot to ask for this turn.
AFTER_SALES_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "model": _STR_OR_NULL,
        "serial_or_year": _STR_OR_NULL,
        "site_city": _STR_OR_NULL,
        "need": _STR_OR_NULL,
        "contact_pref": _STR_OR_NULL,
        "contact_detail": _STR_OR_NULL,
        "asked_slot": _STR_OR_NULL,
        "message": {"type": "string"},
    },
    "required": [
        "model", "serial_or_year", "site_city", "need", "contact_pref", "contact_detail",
        "asked_slot", "message",
    ],
}

# The after-sales slots, in the order they are collected (one question per turn, LLD-AG-04).
# contact_detail (the actual email/phone) is collected AFTER contact_pref so the handoff carries
# everything M6 needs.
AFTER_SALES_SLOTS = ["model", "serial_or_year", "site_city", "need", "contact_pref", "contact_detail"]

# Product-advisor intent parse (LLD-AG-02). `model_or_family` is the model/kit/family the
# visitor named (null if they asked generally); `is_price_or_leadtime` flags a commercial ask
# that must route to handoff, never an answer; `search_query` is an English retrieval phrase.
PRODUCT_QUERY_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "model_or_family": _STR_OR_NULL,
        "is_price_or_leadtime": {"type": "boolean"},
        "search_query": {"type": "string"},
    },
    "required": ["model_or_family", "is_price_or_leadtime", "search_query"],
}

# Query translation (LLD-RT-07): retrieval stays English, so a Hindi/Hinglish free-text query
# is translated to an English search phrase before search_documents. Tool args + citations
# are unaffected; only the retrieval query is translated.
TRANSLATE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"query_en": {"type": "string"}},
    "required": ["query_en"],
}

# The three application slots that must be present before match_capability runs, in ask order.
REQUIRED_SLOTS = ["gas", "capacity", "discharge_p"]
