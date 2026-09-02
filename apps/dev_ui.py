"""Gradio dev chat harness for the Jyotech agent (dev tooling only — LLD-free).

Wraps the **existing** turn entry point (:func:`agentkit.runtime.orchestrator.run_turn`)
in a browser chat UI for internal evaluation. It duplicates no orchestrator logic and
changes no agent/prompt/tool/schema/core-runtime code — the same wiring the CLI ``chat``
command uses (``build_runtime_client``, ``create_session``, ``rebuild_context``) is reused
here.

Safety rails (this must never run public-facing):
  * refuses to start unless ``JYOTECH_DEV_UI=1``;
  * binds ``127.0.0.1`` only; ``share=False`` hardcoded.

Run from the **repo root** so pydantic-settings finds ``.env`` (``env_file=".env"`` is
resolved relative to CWD):

    JYOTECH_DEV_UI=1 python -m apps.dev_ui

The customer-facing widget is LLD-RT-01's FastAPI/SSE component at milestone 7, which
supersedes this harness and is unaffected by it. See ``apps/README.md``.
"""

from __future__ import annotations

import os
import queue
import sys
from pathlib import Path
from typing import Any, Callable

from apps.harness import ERROR, FINAL, STATUS, TOKEN, stream_turn


class _QueueStages:
    """Drain a thread-safe queue of stage labels as a *reusable* iterator.

    The orchestrator's ``on_stage`` fires from the turn's background worker thread while the
    harness polls ``next()`` from the main thread — a :class:`queue.Queue` bridges the two. Each
    ``next()`` pops one pending label or raises ``StopIteration`` when momentarily empty (the
    harness then shows its generic elapsed-seconds line for that poll). Unlike a generator, this
    stays usable after an empty read: a later ``next()`` yields whatever the worker has since put.
    """

    def __init__(self, q: "queue.Queue[str]") -> None:
        self._q = q

    def __iter__(self) -> "_QueueStages":
        return self

    def __next__(self) -> str:
        try:
            return self._q.get_nowait()
        except queue.Empty:
            raise StopIteration

_ENV_GATE = "JYOTECH_DEV_UI"

# --- branding (cosmetic; Jyotech-branded equivalents of the reference build) ------
APP_TITLE = "Jyotech Engineering — Product Information Agent"
SUBTITLE = (
    "Ask about Jyotech products: what exists, how ranges differ, which one fits an "
    "application. Answers come from the published catalogue only."
)
GREETING = (
    "Hi — I'm the Jyotech product assistant. I can help you find products, compare "
    "ranges, and check specifications from the Jyotech catalogue. How can I help you today?"
)
# Quick-start chips — the Jyotech divisions from docs/design/flow.md §S2, phrased as questions.
CHIPS = [
    "What industrial compressors do you have?",
    "What do you offer for fire, rescue & diving?",
    "How do I get service and spares?",
    "Where can I find product documents?",
]
_NO_SOURCES = "_No sources yet._"
_NO_TRACE = "_Trace appears here after each turn._"

# Full-screen chat + no Gradio branding. footer{display:none} hides
# "Built with Gradio · Use via API · Settings"; the container spans full width.
_CSS = """
footer { display: none !important; }
.gradio-container { max-width: 100% !important; }
#chatbox { flex-grow: 1; }
"""


_ICON_SUFFIXES = {".png", ".ico", ".svg", ".jpg", ".jpeg"}


def _favicon_path() -> str | None:
    """Resolve the browser-tab icon from ``apps/assets/`` (relative to this file, not CWD).

    An explicit ``favicon.{png,ico,svg}`` wins; otherwise the first image dropped into
    ``apps/assets/`` is used (dev convenience). Returns ``None`` if none exists, so the app
    still launches.
    """
    assets = Path(__file__).resolve().parent / "assets"
    for ext in ("png", "ico", "svg"):
        candidate = assets / f"favicon.{ext}"
        if candidate.exists():
            return str(candidate)
    if assets.is_dir():
        images = sorted(p for p in assets.iterdir() if p.suffix.lower() in _ICON_SUFFIXES)
        if images:
            return str(images[0])
    return None


def _greeting_history() -> list[dict]:
    """The initial transcript: a single UI-only greeting bubble (never sent to run_turn)."""
    return [{"role": "assistant", "content": GREETING}]


def _patch_gradio_client_bool_schema() -> None:
    """Work around an upstream ``gradio_client`` bug (dev-only, process-local).

    ``gradio_client.utils.get_type`` runs ``if "const" in schema``; when a JSON-schema
    node is a boolean (e.g. ``additionalProperties: true``) this raises
    ``TypeError: argument of type 'bool' is not iterable``. That 500s Gradio's ``/`` config
    route, which in turn makes Gradio's own localhost self-check fail at launch. We make the
    type-hint conversion treat a boolean schema node as ``Any``. This affects only API
    type-hint generation in this process — no effect on the agent's runtime behaviour.
    """
    import gradio_client.utils as gcu

    if getattr(gcu, "_jyotech_bool_schema_patch", False):
        return
    _orig = gcu._json_schema_to_python_type

    def _safe(schema, defs=None):  # recursion re-enters here via the module global
        if isinstance(schema, bool):
            return "Any"
        return _orig(schema, defs)

    gcu._json_schema_to_python_type = _safe
    gcu._jyotech_bool_schema_patch = True


# --- live seams (built once at startup; injected into the adapter) ----------


def _build_seams() -> tuple[Callable[[], Any], Callable[[Any, str], Any]]:
    """Build the live ``session_provider`` / ``turn_runner`` from the CLI's own wiring.

    A fresh DB connection is opened per session-create and per turn (rather than the CLI's
    single long-lived connection), so concurrent Gradio worker threads never share one
    thread-unsafe SQLAlchemy ``Connection``. The runtime LLM client is built once and its
    text-only ``complete_fn`` reused, exactly as the CLI does.
    """
    from agentkit.client_config import gas_alias_map
    from agentkit.db.engine import connect
    from agentkit.ingest.sources import autodetect_client
    from agentkit.retrieval.chunk import resolve_active_release
    from agentkit.runtime.llm import build_runtime_client
    from agentkit.runtime.ops import create_session, rebuild_context
    from agentkit.runtime.orchestrator import Ctx, run_turn

    client = autodetect_client()
    complete_fn = build_runtime_client().complete_fn()
    gas_aliases = gas_alias_map(client)

    def session_provider() -> Any:
        with connect() as conn:
            with conn.begin():
                release_id = resolve_active_release(conn)
                sid = create_session(conn, client, release_id)
        return sid

    def turn_runner(
        session_id: Any, message: str, on_stage: Callable[[str], None] | None = None
    ) -> Any:
        with connect() as conn:
            with conn.begin():
                history = rebuild_context(conn, session_id)
                ctx = Ctx(
                    conn=conn,
                    client=client,
                    complete=complete_fn,
                    embed=None,  # live self-hosted embedder inside search_documents
                    gas_aliases=gas_aliases,
                    on_stage=on_stage,
                )
                return run_turn(ctx, session_id, message, history=history)

    return session_provider, turn_runner


# --- UI ---------------------------------------------------------------------


def _sources_markdown(sources: list[dict] | None) -> str:
    """Render the customer-facing sources: 'title — location' as the link text.

    Each row is ``{title, url, link, location, kind}`` from ``resolve_sources``. The link text is
    the title plus its readable location (page + section for a PDF, the section label for a web
    page); a row with no ``link`` (null url) renders as plain text.
    """
    if not sources:
        return "_No sources cited for this turn._"
    lines = []
    for s in sources:
        title = s.get("title") or "(source)"
        location = s.get("location")
        link = s.get("link")
        text = f"{title} — {location}" if location else title
        lines.append(f"- [{text}]({link})" if link else f"- {text}")
    return "\n".join(lines)


def build_app(session_provider: Callable[[], Any], turn_runner: Callable[[Any, str], Any]):
    """Build the Gradio Blocks app around the injected live seams.

    Full-screen chat with Jyotech branding; the dev affordances (Sources + trace) live in
    collapsed panels below, clearly separated from the transcript. Cosmetic only — the
    streaming/session semantics come entirely from ``apps.harness.stream_turn``.
    """
    import gradio as gr

    with gr.Blocks(
        title=APP_TITLE, fill_height=True, css=_CSS, analytics_enabled=False
    ) as app:
        with gr.Row(equal_height=True):
            with gr.Column():
                gr.Markdown(f"## {APP_TITLE}")
                gr.Markdown(SUBTITLE)
            new_chat = gr.Button("New chat", scale=0, min_width=120)

        chatbot = gr.Chatbot(
            type="messages",
            value=_greeting_history(),
            show_copy_button=True,
            show_label=False,
            elem_id="chatbox",
            scale=1,
        )
        with gr.Row():
            msg = gr.Textbox(
                placeholder="Ask about a product, a range, or an application…",
                show_label=False,
                autofocus=True,
                scale=8,
                container=False,
            )
            send = gr.Button("Send", variant="primary", scale=1, min_width=100)

        gr.Markdown("**Try one**")
        with gr.Row():
            chip_btns = [gr.Button(c, size="sm") for c in CHIPS]

        # Dev affordances below the chat — collapsed, clearly marked internal.
        with gr.Accordion("Sources", open=False):
            sources_md = gr.Markdown(_NO_SOURCES)
        with gr.Accordion("Dev trace (internal evaluation)", open=False):
            gr.Markdown(
                "_Turn diagnostics — same content as `agentkit chat --show-trace`. "
                "Separate from the chat transcript._"
            )
            trace_md = gr.Markdown(_NO_TRACE)

        # per-tab conversation session (holds the session_id created on first message)
        session_state = gr.State({})

        def respond(message: str, history: list[dict], state: dict):
            message = (message or "").strip()
            if not message:
                yield history, gr.update(), gr.update(), ""
                return
            history = list(history) + [
                {"role": "user", "content": message},
                {"role": "assistant", "content": ""},
            ]
            acc = ""
            sources_out: Any = gr.update()
            trace_out: Any = gr.update()
            # Real per-node progress: the orchestrator pushes stage labels onto this queue via
            # ``on_stage``; the harness drains it through ``stage_source``. Falls back to the
            # generic indicator whenever the queue is momentarily empty.
            stage_q: "queue.Queue[str]" = queue.Queue()
            for up in stream_turn(
                message,
                state,
                turn_runner=lambda sid, text: turn_runner(sid, text, on_stage=stage_q.put),
                session_provider=session_provider,
                stage_source=_QueueStages(stage_q),
            ):
                if up.kind == STATUS:
                    history[-1]["content"] = f"_{up.status}_"
                elif up.kind == TOKEN:
                    acc += up.delta or ""
                    history[-1]["content"] = acc
                elif up.kind == ERROR:
                    history[-1]["content"] = up.text or "Error."
                    trace_out = up.trace or ""
                elif up.kind == FINAL:
                    history[-1]["content"] = up.text or ""
                    sources_out = _sources_markdown(up.sources)
                    trace_out = up.trace or ""
                yield history, sources_out, trace_out, ""

        def reset():
            """New chat: fresh transcript + a new session on the next message."""
            return _greeting_history(), {}, _NO_SOURCES, _NO_TRACE, ""

        _turn_inputs = [msg, chatbot, session_state]
        _turn_outputs = [chatbot, sources_md, trace_md, msg]

        msg.submit(respond, inputs=_turn_inputs, outputs=_turn_outputs)
        send.click(respond, inputs=_turn_inputs, outputs=_turn_outputs)
        for chip_text, btn in zip(CHIPS, chip_btns):
            # fill the input with the chip text, then run the same turn handler
            btn.click(lambda c=chip_text: c, outputs=msg).then(
                respond, inputs=_turn_inputs, outputs=_turn_outputs
            )
        new_chat.click(
            reset, outputs=[chatbot, session_state, sources_md, trace_md, msg]
        )

    return app


def main(argv: list[str] | None = None) -> int:
    """Entry point. Refuses to start unless the dev gate env var is set."""
    if os.environ.get(_ENV_GATE) != "1":
        sys.stderr.write(
            f"refusing to start: {_ENV_GATE}=1 is required. This harness is dev-only and\n"
            "must never run public-facing. Run from the repo root:\n"
            f"    {_ENV_GATE}=1 python -m apps.dev_ui\n"
        )
        return 2

    _patch_gradio_client_bool_schema()
    session_provider, turn_runner = _build_seams()
    app = build_app(session_provider, turn_runner)
    # 127.0.0.1 only, never a public share link; no API page; branded favicon if present.
    app.launch(
        server_name="127.0.0.1",
        share=False,
        inbrowser=False,
        show_api=False,
        favicon_path=_favicon_path(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
