"""Commercial Routing agent (LLD-AG-05).

Price, lead-time, dealer/distributor and export enquiries. It **always** hands off with
``lead_type ∈ {commercial, dealer}`` and **never states or estimates a price or lead time** —
the reply is a fixed, language-appropriate template with **no figures at all**, so the
guarantee holds by construction and does not lean on the numeric guard (which is only a
backstop). Tool: ``get_office`` (06), used to name the covering team.

The reply is in the visitor's language (Hinglish stays Hinglish, LLD-RT-07). No ``ops.lead``
row is written in 5b (lead capture is milestone 6).
"""

from __future__ import annotations

from agentkit.runtime.agents.base import AgentOutput
from agentkit.runtime.ops import MessageRecord

# Dealer/distributor asks route as `dealer`; everything else (price, lead time, export) as
# `commercial`.
_DEALER_KEYWORDS = ("dealer", "distributor", "reseller", "channel partner", "distributorship")

# Language-appropriate, number-free handoff templates. `{team}` is the team to connect to.
_TEMPLATES = {
    "en": (
        "We don't share prices or lead times here, but I can connect you with our {team} "
        "who'll get you accurate details — shall I put you in touch?"
    ),
    "hi": (
        "हम यहाँ कीमत या डिलीवरी समय साझा नहीं करते, लेकिन मैं आपको हमारी {team} से जोड़ सकता हूँ "
        "जो आपको सही जानकारी देंगे — क्या मैं आपको उनसे जोड़ूँ?"
    ),
    "hinglish": (
        "Hum yahan price ya lead time nahi dete, lekin main aapko hamari {team} se connect kar "
        "sakta hoon jo aapko sahi details denge — kya main aapko unse jodun?"
    ),
}
_TEAM = {
    "commercial": {"en": "commercial team", "hi": "कमर्शियल टीम", "hinglish": "commercial team"},
    "dealer": {
        "en": "dealer network",
        "hi": "डीलर नेटवर्क",
        "hinglish": "dealer network",
    },
}


def _lead_type(text: str) -> str:
    q = text.lower()
    return "dealer" if any(k in q for k in _DEALER_KEYWORDS) else "commercial"


def run(ctx, *, prompt_body, triage, history, latest_user, tools) -> AgentOutput:
    language = triage.get("language", "en")
    lang = language if language in _TEMPLATES else "en"
    lead_type = _lead_type(latest_user)

    # Name the covering team via the head office (tool 06); message carries no figure.
    tools.get_office()

    team = _TEAM[lead_type][lang]
    msg = _TEMPLATES[lang].format(team=team)
    return AgentOutput(
        action="handoff",
        messages=[MessageRecord("assistant", "text", msg)],
        output={"action": "handoff", "lead_type": lead_type},
    )
