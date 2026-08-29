"""Language handling (LLD-RT-07).

Triage detects the visitor's language; agents are told to respond in it via ``respond_in``.
Retrieval stays in English (the corpus is English), so the query passed to
``search_documents`` is the English form. For 5a this is a light pass-through: the English
retrieval query is the agent's own English search phrase (agents build their queries in
English), and ``respond_in`` injects the response-language instruction into the agent prompt.
Full query translation for Hindi/Hinglish free-text retrieval is a later refinement.
"""

from __future__ import annotations

_LANGUAGE_NAMES = {"en": "English", "hi": "Hindi", "hinglish": "Hinglish (romanised Hindi/English)"}


def respond_in(language: str | None) -> str:
    """A one-line instruction telling an agent which language to reply in."""
    name = _LANGUAGE_NAMES.get((language or "en").lower(), "English")
    return f"Respond in {name}."
