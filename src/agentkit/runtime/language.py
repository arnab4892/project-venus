"""Language handling (LLD-RT-07).

Triage detects the visitor's language; agents are told to respond in it via ``respond_in``.
Retrieval stays in English (the corpus is English), so the query passed to
``search_documents`` is the English form. For 5a this is a light pass-through: the English
retrieval query is the agent's own English search phrase (agents build their queries in
English), and ``respond_in`` injects the response-language instruction into the agent prompt.
Full query translation for Hindi/Hinglish free-text retrieval is a later refinement.
"""

from __future__ import annotations

from agentkit.extract.llm import CompleteFn, call_json
from agentkit.runtime.schemas import TRANSLATE_SCHEMA

# The response-language line is the LAST thing the compose model reads, so it must be a firm,
# self-sufficient instruction in EVERY language — not a bare label. The agent prompts carry worked
# examples in English, Hinglish and Devanagari; without a firm last-position instruction the model
# imitates whichever example reads most vividly and drifts into that example's language (a live
# regression: an English turn recited the Hinglish exemplar). So each language closes with the same
# clause — examples show structure only; the reply language comes ONLY from this instruction — at
# equal strength. Hindi additionally restates its written-Devanagari register, where recency matters
# most (LLD-RT-07).
_LANGUAGE_ONLY_TAIL = (
    "Whatever language the prompt's example answers use, they illustrate structure and format only; "
    "the reply language comes ONLY from this instruction."
)
_ENGLISH_RESPONSE = (
    "Reply in English only. Do not use Hindi or Hinglish words or phrasing. " + _LANGUAGE_ONLY_TAIL
)
_HINGLISH_RESPONSE = (
    "Reply in Hinglish — romanised Hindi/English in Latin script, the way an Indian sales engineer "
    "speaks. No Devanagari script, and do not drift into pure English. " + _LANGUAGE_ONLY_TAIL
)
_HINDI_RESPONSE = (
    "Respond in written Devanagari Hindi throughout — the way a Hindi newspaper prints it.\n"
    "Latin script ONLY for exact product/family/model/brand names (in bold), units and standard "
    "codes; every other word is Hindi, and all figures are ASCII digits. " + _LANGUAGE_ONLY_TAIL
)
_RESPONSES = {"en": _ENGLISH_RESPONSE, "hi": _HINDI_RESPONSE, "hinglish": _HINGLISH_RESPONSE}


def respond_in(language: str | None) -> str:
    """An instruction telling an agent which language to reply in (LLD-RT-07).

    Every language gets a firm, self-sufficient last-position instruction of equal strength — a bare
    label let exemplar imitation override it (an English turn once recited the Hinglish exemplar). En
    forbids Hindi/Hinglish; Hinglish pins the romanised register (no Devanagari, no pure-English
    drift); Hindi gets a two-line written-Devanagari register reminder. All three close with the same
    clause: examples show structure only, the reply language comes only from here. Unknown/None →
    English. Citations/locators are unchanged in every case.
    """
    lang = (language or "en").lower()
    return _RESPONSES.get(lang, _ENGLISH_RESPONSE)


def english_query(complete: CompleteFn, query: str, language: str | None) -> str:
    """Translate a Hindi/Hinglish free-text query to an English search phrase (LLD-RT-07).

    The corpus is English, so retrieval queries and tool args stay English: for ``hi``/
    ``hinglish`` this does one small structured LLM call to get an English search phrase; for
    English (or any failure) it returns the query unchanged. Citations/locators are unaffected —
    only the ``search_documents`` query string is translated.
    """
    lang = (language or "en").lower()
    if lang not in ("hi", "hinglish") or not query.strip():
        return query
    messages = [
        {
            "role": "system",
            "content": (
                "Translate the user's product/company search query into a concise ENGLISH "
                "search phrase for a document search. Keep model names, standards and numbers "
                "as-is. Return JSON {\"query_en\": <phrase>}."
            ),
        },
        {"role": "user", "content": query},
    ]
    try:
        raw = call_json(
            client=None,
            complete=complete,
            messages=messages,
            json_schema=TRANSLATE_SCHEMA,
            schema_name="translate",
            kind="translate",
            section_id=None,
        )
    except Exception:  # noqa: BLE001 - a translation hiccup must not crash the turn
        return query
    return (raw.get("query_en") or "").strip() or query
