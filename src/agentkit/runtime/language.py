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

_LANGUAGE_NAMES = {"en": "English", "hi": "Hindi", "hinglish": "Hinglish (romanised Hindi/English)"}


def respond_in(language: str | None) -> str:
    """A one-line instruction telling an agent which language to reply in.

    A Hinglish (romanised Hindi/English) message is answered in Hinglish — Latin script stays
    Latin script; citations/locators are unchanged (LLD-RT-07).
    """
    name = _LANGUAGE_NAMES.get((language or "en").lower(), "English")
    return f"Respond in {name}."


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
