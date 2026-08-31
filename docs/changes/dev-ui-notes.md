# Dev-tooling note — Gradio dev chat harness

**Date:** 2026-08-31 · **Branch:** `gradio-dev-ui` (off `milestone-5b`) · **Scope:** dev
tooling only.

## What

A browser chat harness for internal evaluation of the Jyotech agent, added under `apps/`:

- `apps/dev_ui.py` — a full-screen, Jyotech-branded Gradio (`gr.Blocks`) chat UI (title/
  subtitle header, greeting bubble, Send + "Try one" quick-start chips + New chat; Gradio
  branding hidden; favicon from `apps/assets/`; Sources + Dev-trace as collapsed panels
  below the chat). All chrome is cosmetic — no agent/prompt/core change.
- `apps/assets/` — dev-only static assets (the favicon).
- `apps/harness.py` — a UI-agnostic adapter (session-id handling + the streaming
  generator + Sources/trace builders), imports no gradio/agentkit at module load so it is
  unit-testable with the turn function mocked.
- `apps/README.md` — marks the directory dev-only.
- `tests/apps/test_dev_ui_adapter.py` — adapter tests (turn function mocked; no live LLM).
- `pyproject.toml` — new `ui` optional-dependency extra (`gradio>=5,<6`).

## Why

The CLI `agentkit chat` REPL is the only way to drive a live turn today. The team needs a
richer surface for internal evaluation — per-tab sessions, visible citations, and the
`--show-trace` diagnostics in a side panel — without waiting for the milestone-7 widget.

## Not a requirement

This is **not** a PRD/HLD/LLD item and adds **no** traceability row. It reuses the existing
turn entry point `agentkit.runtime.orchestrator.run_turn` unchanged and touches **no**
agent/prompt/tool/schema or core-runtime file (`orchestrator.py`, `grounding.py`, `llm.py`
are untouched). The customer-facing chat widget remains LLD-RT-01's FastAPI/SSE component
at **milestone 7**, which supersedes this harness and is unaffected by it.

## Design points worth recording

- **Grounding gate untouched (LLD-RT-05).** The harness calls `run_turn`, whose graph runs
  the gate over the complete draft before returning. Only the **gated** final text is
  streamed to the UI, typewritered word-by-word. Raw LLM tokens are never streamed.
- **Honest stage events.** While a turn runs, a single generic indicator
  (`working on your answer… (Ns)`) is shown — no fabricated per-node labels, because the
  orchestrator exposes no node-boundary hook today. The indicator is fed from a *pluggable*
  `stage_source`; a later **read-only** `on_stage` orchestrator callback (deferred until
  after `milestone-5b` merges) can supply real boundary labels with no other UI change.
  No orchestrator edit was made on this branch.
- **Concurrency.** A fresh DB connection is opened per session-create and per turn (not the
  CLI's single long-lived connection) so concurrent Gradio worker threads never share one
  thread-unsafe SQLAlchemy `Connection`.
- **Never public-facing.** Refuses to start without `JYOTECH_DEV_UI=1`; binds `127.0.0.1`;
  `share=False` hardcoded.
- **Own venv per worktree.** Setup documented in `apps/README.md` — create this worktree's
  `.venv` from the uv-managed 3.12 interpreter and `pip install -e '.[ui,dev]'`; never
  editable-install this branch into another checkout's venv.
