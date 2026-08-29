"""Deflect / holding agent (LLD-AG deflect path).

Handles out-of-scope messages and, in 5a, intents whose full agent is not yet built
(product_question, after_sales, commercial → their agents land in 5b). It produces a polite
scope statement with no tool calls and no factual claims, so no grounding is required — but the
turn is still recorded in ``ops`` like any other.
"""

from __future__ import annotations

from agentkit.extract.llm import call_json
from agentkit.runtime.agents.base import AgentOutput
from agentkit.runtime.language import respond_in
from agentkit.runtime.ops import MessageRecord
from agentkit.runtime.schemas import DEFLECT_SCHEMA
from agentkit.runtime.triage import build_chat_messages


def run(ctx, *, prompt_body, triage, history, latest_user, tools) -> AgentOutput:
    language = triage.get("language", "en")
    messages = build_chat_messages(prompt_body + "\n\n" + respond_in(language), history, latest_user)
    raw = call_json(
        client=None,
        complete=ctx.complete,
        messages=messages,
        json_schema=DEFLECT_SCHEMA,
        schema_name="deflect",
        kind="deflect",
        section_id=None,
    )
    return AgentOutput(
        action="deflect",
        messages=[MessageRecord("assistant", "text", raw.get("message", ""))],
        output=raw,
    )
