"""Dev-harness adapter — UI-agnostic glue over the agent's existing turn entry point.

This module deliberately imports **no gradio, no agentkit runtime, no DB** at import
time. The live seams — creating a session and running one turn — are injected as
callables (``session_provider`` / ``turn_runner``), so the adapter is unit-tested with
them mocked: no gradio, no Postgres, no live LLM.

Streaming contract (mirrors LLD-RT-05 without touching it): the turn runs to completion
in a background thread. The grounding gate runs *inside* ``turn_runner`` (it is
``run_turn``), so by the time it returns the full draft has already been vetted and only
the **gated final text** is typewritered to the UI. Raw LLM tokens are never streamed.

Stage events are intentionally honest: a single generic "working on your answer… (Ns)"
indicator, sourced from a *pluggable* ``stage_source`` iterator. A later, read-only
``on_stage`` orchestrator callback (deferred until after milestone-5b merges) can feed
that iterator with real node-boundary labels — no other change here required. We never
show fabricated per-node labels for activity that isn't actually observed.
"""

from __future__ import annotations

import json
import re
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, Optional

# ``Update.kind`` values.
STATUS = "status"  # generic working indicator line
TOKEN = "token"  # one typewriter word chunk of the gated final text
FINAL = "final"  # terminal success: full text + sources + trace
ERROR = "error"  # terminal failure: message + traceback (never a swallowed exception)

_GENERIC_STATUS = "working on your answer"


@dataclass
class Update:
    """One streamed UI update. ``kind`` drives how the front-end renders it."""

    kind: str
    status: str | None = None  # STATUS: generic indicator line
    delta: str | None = None  # TOKEN: next word chunk (typewriter)
    text: str | None = None  # FINAL/ERROR: full assistant text / error message
    sources: list[dict] | None = None  # FINAL: deduplicated Sources rows
    trace: str | None = None  # FINAL: markdown trace; ERROR: the traceback


# --- session id handling ---------------------------------------------------


def get_or_create_session(state: dict, session_provider: Callable[[], Any]) -> Any:
    """Return this tab's session id, creating it once via ``session_provider``.

    ``state`` is the per-tab Gradio session-state dict. The id is created on the first
    message and reused for every later turn in the same tab (one conversation per tab).
    """
    sid = state.get("session_id")
    if sid is None:
        sid = session_provider()
        state["session_id"] = sid
    return sid


# --- rendering helpers -----------------------------------------------------


def _iter_words(text: str) -> Iterator[str]:
    """Yield ``text`` in word-sized chunks, keeping trailing whitespace on each.

    Re-joining the chunks reproduces ``text`` exactly, so the typewriter never drops or
    duplicates characters.
    """
    for tok in re.findall(r"\S+\s*|\s+", text):
        yield tok


def render_messages(messages: list[dict]) -> str:
    """Flatten ``RunResult.messages`` into one markdown string for the chat bubble.

    ``document_card`` bubbles are rendered as ``📄 title — locator — url`` (the CLI's
    convention); plain bubbles contribute their ``text``.
    """
    parts: list[str] = []
    for m in messages or []:
        if m.get("kind") == "document_card":
            pl = m.get("payload") or {}
            bits = [
                b
                for b in (
                    "📄 " + str(pl.get("title") or "").strip(),
                    (str(pl.get("locator")).strip() if pl.get("locator") else ""),
                    (str(pl.get("url")).strip() if pl.get("url") else ""),
                )
                if b and b != "📄 "
            ]
            line = " — ".join(bits).strip()
            if line:
                parts.append(line)
        else:
            txt = (m.get("text") or "").strip()
            if txt:
                parts.append(txt)
    return "\n\n".join(parts)


def build_sources(result: Any) -> list[dict]:
    """The customer-facing Sources rows for a turn.

    The runtime resolver (``agentkit.runtime.sources.resolve_sources``) already ran inside the
    turn and stored its ``{title, url, link, location, kind}`` list on the answer message's
    payload (``payload["sources"]``) — the same list persisted to ``ops.message`` and shown to the
    customer. We simply surface it here; the harness stays DB-free and does not re-resolve.
    Non-answer turns (clarify/deflect/slot) carry no sources → ``[]``.
    """
    for m in getattr(result, "messages", None) or []:
        payload = m.get("payload") or {}
        sources = payload.get("sources")
        if sources:
            return list(sources)
    return []


def build_trace(result: Any) -> str:
    """Markdown mirroring ``cli._print_trace`` — same content as ``chat --show-trace``."""
    tri = getattr(result, "triage", None) or {}
    lines: list[str] = [
        f"**turn** `{getattr(result, 'turn_id', None)}` · outcome=`{getattr(result, 'outcome', None)}`"
        f" · routed=`{getattr(result, 'route', None)}`",
        "**triage** division=`{}` intent=`{}` language=`{}` in_scope=`{}` confidence=`{}`".format(
            tri.get("division"),
            tri.get("intent"),
            tri.get("language"),
            tri.get("in_scope"),
            tri.get("confidence"),
        ),
    ]
    for inv in getattr(result, "invocations", None) or []:
        lines.append(f"- **agent_invocation** `{inv.get('agent')}` prompt=`{inv.get('prompt_id')}`")
        for tc in inv.get("tool_calls") or []:
            args = json.dumps(tc.get("args"), default=str)
            lines.append(
                f"    - tool_call `{tc.get('tool')}` args=`{args}` rows=`{tc.get('rows_returned')}`"
            )
    if getattr(result, "grounding", None):
        lines.append(f"**grounding** `{json.dumps(result.grounding, default=str)}`")
    for c in getattr(result, "citations", None) or []:
        lines.append(
            f"- citation `{c.get('kind')}` `{c.get('ref_id')}` {c.get('locator') or ''}".rstrip()
        )
    return "\n".join(lines)


# --- streaming -------------------------------------------------------------


def stream_turn(
    message: str,
    state: dict,
    *,
    turn_runner: Callable[[Any, str], Any],
    session_provider: Callable[[], Any],
    stage_source: Optional[Iterable[str]] = None,
    clock: Callable[[], float] = time.monotonic,
    poll: float = 0.1,
    token_delay: float = 0.03,
) -> Iterator[Update]:
    """Drive one turn, yielding Updates: STATUS… then TOKEN… then FINAL — or ERROR.

    - ``turn_runner(session_id, message) -> RunResult`` runs the real turn (the grounding
      gate runs inside it). It executes in a background thread so status updates can be
      emitted while it runs; any exception it raises is captured and surfaced as a single
      terminal ERROR update (never a swallowed exception, never an endless "working…").
    - ``session_provider() -> session_id`` creates the tab session on first use.
    - ``stage_source``: optional iterator of status strings — the seam the read-only
      ``on_stage`` queue plugs into. Labels are **sticky** and accumulate into a trail
      (completed stages prefixed ✓, the current one last with the ticking timer); an empty
      poll keeps the current label rather than reverting. Without a source (or before the
      first label) a single honest generic indicator with elapsed seconds is shown.
    - ``token_delay``: seconds slept after each TOKEN emission so the typewriter phase
      actually paces out (Gradio repaints per yield); without it every word lands in one
      frame and the answer appears in a single paint. Tests pass ``0`` to stay fast — the
      ordering assertions are unaffected.

    An initial STATUS update is always emitted before the first thread join, so the
    ordering (status → final/error) is deterministic even when the turn returns instantly.
    """
    sid = get_or_create_session(state, session_provider)

    box: dict[str, Any] = {}

    def _work() -> None:
        try:
            box["result"] = turn_runner(sid, message)
        except BaseException as exc:  # noqa: BLE001 — captured & surfaced, never swallowed
            box["error"] = exc
            box["traceback"] = traceback.format_exc()

    worker = threading.Thread(target=_work, daemon=True)
    start = clock()
    worker.start()

    src = iter(stage_source) if stage_source is not None else None
    # Sticky stage trail: labels accumulate here (in arrival order); the last is the current
    # stage, the earlier ones are completed. A label stays shown until the next one arrives — an
    # empty poll never reverts to the generic line (that only renders before the first label).
    trail: list[str] = []

    def _drain() -> None:
        if src is None:
            return
        while True:
            try:
                trail.append(next(src))
            except StopIteration:
                break

    def _status() -> Update:
        _drain()
        elapsed = int(clock() - start)
        if not trail:
            return Update(kind=STATUS, status=f"{_GENERIC_STATUS}… ({elapsed}s)")
        done = " · ".join(f"✓ {label}" for label in trail[:-1])
        current = f"{trail[-1]}… ({elapsed}s)"
        return Update(kind=STATUS, status=f"{done} · {current}" if done else current)

    yield _status()
    while worker.is_alive():
        worker.join(poll)
        if worker.is_alive():
            yield _status()

    if "error" in box:
        exc = box["error"]
        yield Update(
            kind=ERROR,
            text=f"Something went wrong while answering ({type(exc).__name__}). "
            "See the trace panel for details.",
            trace="```\n" + (box.get("traceback") or "") + "\n```",
        )
        return

    result = box["result"]
    final_text = render_messages(getattr(result, "messages", None) or [])
    for word in _iter_words(final_text):
        yield Update(kind=TOKEN, delta=word)
        if token_delay:
            time.sleep(token_delay)
    yield Update(
        kind=FINAL,
        text=final_text,
        sources=build_sources(result),
        trace=build_trace(result),
    )
