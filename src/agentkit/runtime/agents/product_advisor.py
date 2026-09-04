"""Product Advisor agent (LLD-AG-02).

Answers questions about a named model / kit / variant (e.g. MCH-16, EOLO 330) or a division's
range. Tools: ``get_product`` (03), ``list_products`` (02), ``search_documents`` (04). It:

* parses the visitor's message into ``{model_or_family, is_price_or_leadtime, search_query}``;
* a **price / lead-time** ask is never answered — it routes to a commercial handoff (no number);
* a named model/family → ``get_product`` (exact model names; a null-``model_name`` product is
  presented by family + variant, LLD-EXT-06); otherwise → ``list_products`` for the division;
* ``search_documents`` (English query, division-scoped) adds supporting prose;
* the answer is composed only from tool results and grounded downstream (LLD-RT-05).

The query passed to ``search_documents`` is English even for a Hindi/Hinglish message
(``english_query``, LLD-RT-07); the reply is in the visitor's language (``respond_in``).
"""

from __future__ import annotations

from agentkit.extract.llm import call_json
from agentkit.runtime.agents.base import AgentOutput, compose_grounded_answer
from agentkit.runtime.language import english_query
from agentkit.runtime.ops import MessageRecord
from agentkit.runtime.schemas import PRODUCT_QUERY_SCHEMA
from agentkit.runtime.triage import build_chat_messages

# Fixed, per-language price/lead-time handoff (LLD-RT-07), selected by detected language and
# carrying no figure by construction. Deterministic + pre-vetted; technical terms stay English.
_PRICE_HANDOFF_TEXTS = {
    "en": (
        "We don't publish prices or lead times here, but I can connect you with our commercial "
        "team who'll get you an accurate quotation — shall I?"
    ),
    "hi": (
        "हम यहाँ कीमत या डिलीवरी समय नहीं देते, लेकिन मैं आपको हमारी कमर्शियल टीम से जोड़ सकता हूँ "
        "जो आपको सटीक कोटेशन देंगे — क्या मैं जोड़ूँ?"
    ),
    "hinglish": (
        "Hum yahan price ya lead time nahi dete, lekin main aapko hamari commercial team se connect "
        "kar sakta hoon jo aapko accurate quotation denge — kya main jodun?"
    ),
}
# Back-compat alias (English is the historical constant).
_PRICE_HANDOFF = _PRICE_HANDOFF_TEXTS["en"]

# A focused extraction prompt (kept out of the compose prompt so the model returns a clean
# lookup key, not a paraphrase). Reliable extraction is what lets get_product resolve a named
# product and its family id be cited.
_PARSE_SYSTEM = (
    "From the visitor's latest message, extract what Jyotech product they are asking about.\n"
    "- model_or_family: the exact product / model / kit / family NAME only, with generic words "
    "removed (drop 'compressor', 'unit', 'system', 'set', 'for diving', 'for rescue', 'breathing' "
    "unless part of the proper name). Examples: 'Do you supply a C-Monitor for diving?' → "
    "'C-Monitor'; 'Tell me about the Diablo Piz escape breathing set' → 'Diablo Piz'; 'the CCDU "
    "compressor' → 'CCDU'; 'your fill containment cabinets' → 'Fill Containment Cabinets'. Use "
    "null when the visitor asks generally about a category/division (e.g. 'what industrial "
    "compressors do you make', 'show me your diving equipment') OR when compare_items is populated.\n"
    "- is_price_or_leadtime: true if they ask price, cost, discount or lead/delivery time.\n"
    "- search_query: a concise English search phrase for the request.\n"
    "- compare_items: when the visitor EXPLICITLY asks to compare or contrast two or more NAMED "
    "products/families ('compare X and Y', 'X vs Y', 'difference between X and Y'), the list of "
    "those exact product/family names (2–4 items, generic words removed as above); an empty list "
    "otherwise. A single product with sibling variants is NOT a comparison — leave it empty and "
    "put the product in model_or_family."
)

# Compose directive for the explicit-comparison table (LLD-AG-02 presentation). Rows carry the
# figure+unit together; a cell is filled ONLY from this turn's tool results; a row absent for one
# item is dropped (never an empty / "Not specified" cell); each item's source is cited.
_COMPARE_INSTRUCTION = (
    "The visitor asked to COMPARE the named products/families. Compose a single Markdown "
    "comparison table: one COLUMN per compared product (its exact bold display name from the tool "
    "results), one ROW per attribute, each figure kept together with its unit in the cell. Fill a "
    "cell ONLY from that product's tool results — never from memory, never invent a figure. If an "
    "attribute is published for one product but not another, OMIT that row entirely; never write "
    "'Not specified' or leave a cell blank. Cite the source each product's figures came from. "
    "Close with one short sentence naming the key difference and a single question that moves the "
    "enquiry forward."
)


def run(ctx, *, prompt_body, triage, history, latest_user, tools) -> AgentOutput:
    language = triage.get("language", "en")

    messages = build_chat_messages(_PARSE_SYSTEM, history, latest_user)
    parse = call_json(
        client=None,
        complete=ctx.complete,
        messages=messages,
        json_schema=PRODUCT_QUERY_SCHEMA,
        schema_name="product_query",
        kind="product_query",
        section_id=None,
    )

    # Price / lead-time is commercial — never answer it here; hand off with no figure.
    if parse.get("is_price_or_leadtime"):
        lang = language if language in _PRICE_HANDOFF_TEXTS else "en"
        return AgentOutput(
            action="handoff",
            messages=[MessageRecord("assistant", "text", _PRICE_HANDOFF_TEXTS[lang])],
            output={"action": "handoff", "lead_type": "commercial", "reason": "price_or_leadtime"},
        )

    division = triage.get("division")
    division = division if division in ("industrial", "fire_rescue", "diving") else None

    # Explicit comparison of 2+ named products/families (LLD-AG-02 multi-fetch). Ground each
    # compared item BY DESIGN, not by retrieval co-occurrence luck: a structured lookup per item
    # plus a TARGETED retrieval per item so each item's own specs surface and are citable to its
    # own source. Product-level items (a model with rows, e.g. MCH-16) resolve via get_product and
    # scope their search to the matched family; family-level items (the process / gas families
    # carry capability rows but NO product rows, so get_product can't see them) are grounded by the
    # division listing and searched division-scoped on the item name. Fewer than two items, or no
    # division, → fall through to the standard single/overview path (co-occurrence fallback,
    # unchanged). The listing is force-cited so every compared family is grounded per item.
    compare_items = [c.strip() for c in (parse.get("compare_items") or []) if c and c.strip()][:4]
    if len(compare_items) >= 2 and division is not None:
        item_family: dict[str, str] = {}
        structured_recs = []
        for item in compare_items:
            rec = tools.get_product(item)
            prods = rec.result.get("products") or []
            if prods:
                structured_recs.append(rec)
                item_family[item] = prods[0]["family_id"]
        listing_rec = tools.list_products(division)
        for item in compare_items:
            q = english_query(ctx.complete, f"{item} specifications", language)
            fam = item_family.get(item)
            try:
                if fam:
                    tools.search_documents(q, family_ids=[fam], k=3)
                else:
                    tools.search_documents(q, division=division, k=3)
            except Exception:  # noqa: BLE001 - missing retrieval infra ≠ a failure
                pass
        message, citations = compose_grounded_answer(
            complete=ctx.complete,
            prompt_body=prompt_body,
            history=history,
            latest_user=latest_user,
            records=tools.records,
            language=language,
            extra_instruction=_COMPARE_INSTRUCTION,
        )
        # Force-cite each item's structured lookup + the division listing, so every compared
        # family is grounded (a citation per compared item), not left to the compose LLM.
        for rec in [*structured_recs, listing_rec]:
            if rec.tr_id not in citations:
                citations.append(rec.tr_id)
        return AgentOutput(
            action="answer",
            draft_text=message,
            citations=citations,
            output={"action": "answer", "compare_items": compare_items},
        )

    # Structured lookup (exact names + citable product/family ids), force-cited below so the
    # family id is always grounded. THEN always search the documents too — a question can ask
    # about a spec or a compliance point (EIGA, oil-free cleaning) that the product row doesn't
    # carry, so retrieval must actually look rather than concede from the name list alone (the
    # absence protocol, applied proactively).
    model_or_family = parse.get("model_or_family")
    structured_rec = None
    scope_family = None
    if model_or_family:
        rec = tools.get_product(model_or_family)
        products = rec.result.get("products") or []
        if products:
            structured_rec = rec
            scope_family = products[0]["family_id"]
    # Fall back to the division listing whenever no product resolved — a generic ask (null name)
    # OR a name get_product cannot match (e.g. "hydrogen fuelling systems", a family carrying no
    # product rows). This is NOT an `elif`: without the fallback an unresolved name leaves the
    # answer with no structured product/family source, so the family id is grounded only if the
    # compose LLM happens to list it — which it intermittently omits (cap-hydrogen-fuelling flake).
    # The listing record is force-cited below, guaranteeing the family is grounded either way.
    if structured_rec is None and division is not None:
        rec = tools.list_products(division)
        if rec.result.get("families"):
            structured_rec = rec

    # Retrieval query: the family/model name (when known) + the question, scoped to the matched
    # family when we have one, else the division. Best-effort — missing infra ≠ a failure.
    fam_name = structured_rec.result["products"][0].get("family_name", "") if scope_family else ""
    query = english_query(
        ctx.complete, f"{fam_name} {parse.get('search_query') or latest_user}".strip(), language
    )
    try:
        if scope_family:
            tools.search_documents(query, family_ids=[scope_family], k=3)
        else:
            tools.search_documents(query, division=division, k=3)
    except Exception:  # noqa: BLE001 - missing retrieval infra ≠ a failure
        pass

    message, citations = compose_grounded_answer(
        complete=ctx.complete,
        prompt_body=prompt_body,
        history=history,
        latest_user=latest_user,
        records=tools.records,
        language=language,
        extra_instruction=(
            "Use the exact model names from the tool results. Describe the product from BOTH the "
            "family fields (family name, summary, attributes) AND the published specifications in "
            "the document-search results (e.g. F.A.D., operating pressures, drive/prime mover, "
            "options) — cite the chunk you take specs from. A product with no printed model number "
            "is referred to by its family name and variant, never a blank or invented name. Only "
            "say the details aren't published if there are neither family fields nor document "
            "results; never claim there are no details while a family or a matching chunk is present."
        ),
    )
    # Deterministically cite the structured lookup when it supplied the answer: the product /
    # family it returned is the source used, so its id is grounded even if the compose LLM
    # forgets to list it (honest — the tool WAS used, not an unused source).
    if structured_rec is not None and structured_rec.tr_id not in citations:
        citations.append(structured_rec.tr_id)
    return AgentOutput(
        action="answer",
        draft_text=message,
        citations=citations,
        output={"action": "answer", "model_or_family": model_or_family},
    )
