"""Triage classifier node (LLD-RT-04).

Classifies the visitor's latest message via the runtime LLM into
``{division, intent, language, in_scope, pii_present, confidence}`` using a strict
``json_schema`` (:data:`~agentkit.runtime.schemas.TRIAGE_SCHEMA`). Confidence < 0.6 is a
signal the orchestrator turns into a one-line clarifying question (outcome ``clarify``).

The call goes through the shared ``complete=`` seam (:func:`agentkit.extract.llm.call_json`),
so tests inject canned JSON and never touch the network; the live path uses the self-hosted
runtime client.
"""

from __future__ import annotations

from agentkit.extract.llm import CompleteFn, call_json
from agentkit.runtime.schemas import DIVISIONS, INTENTS, LANGUAGES, TRIAGE_SCHEMA

CLARIFY_THRESHOLD = 0.6


def build_chat_messages(
    system: str, history: list[dict], latest_user: str
) -> list[dict[str, str]]:
    """Assemble OpenAI chat messages: system prompt + prior text turns + the latest message."""
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for m in history:
        if m.get("kind") not in (None, "text"):
            continue
        role = m.get("role")
        if role not in ("user", "assistant") or not m.get("text"):
            continue
        messages.append({"role": role, "content": m["text"]})
    messages.append({"role": "user", "content": latest_user})
    return messages


def _coerce(raw: dict) -> dict:
    """Defensive normalisation: fill/validate fields a live model might fumble."""
    division = raw.get("division")
    if division not in DIVISIONS:
        division = "unknown"
    intent = raw.get("intent")
    if intent not in INTENTS:
        intent = "faq"
    language = raw.get("language")
    if language not in LANGUAGES:
        language = "en"
    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    out_of_scope = intent == "out_of_scope"
    return {
        "division": division,
        "intent": intent,
        "language": language,
        "in_scope": bool(raw.get("in_scope", not out_of_scope)) and not out_of_scope,
        "pii_present": bool(raw.get("pii_present", False)),
        "confidence": confidence,
    }


def run_triage(
    *,
    complete: CompleteFn,
    prompt_body: str,
    history: list[dict],
    latest_user: str,
) -> dict:
    """Return the coerced triage dict for the latest user message."""
    messages = build_chat_messages(prompt_body, history, latest_user)
    raw = call_json(
        client=None,
        complete=complete,
        messages=messages,
        json_schema=TRIAGE_SCHEMA,
        schema_name="triage",
        kind="triage",
        section_id=None,
    )
    return _coerce(raw)


def needs_clarification(triage: dict) -> bool:
    return triage.get("confidence", 0.0) < CLARIFY_THRESHOLD
