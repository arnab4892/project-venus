# apps/ — dev-only tooling

> **Dev-only. Not shipped.** Nothing here is part of the `agentkit` wheel or a
> PRD/HLD/LLD requirement. The customer-facing chat widget is LLD-RT-01's FastAPI/SSE
> component at **milestone 7**, which **supersedes** the harness below. This directory
> exists so the team can exercise and evaluate the agent in a browser during development.

## `dev_ui.py` — Gradio dev chat harness

A browser chat UI that wraps the **existing** turn entry point
(`agentkit.runtime.orchestrator.run_turn`) — it duplicates no orchestrator logic and
changes no agent/prompt/tool/schema/core-runtime code. It reuses the exact wiring the CLI
`agentkit chat` command uses (`build_runtime_client`, `create_session`,
`rebuild_context`).

What it gives you over the CLI REPL:

- **Full-screen, Jyotech-branded chat.** Title/subtitle header, a static greeting bubble,
  a **Send** button, "Try one" quick-start chips, and a **New chat** button that starts a
  fresh session. Gradio's own branding ("Built with Gradio" / API / Settings footer) is
  hidden; the browser-tab title and favicon are Jyotech's (see `assets/`). All cosmetic —
  no agent/prompt/core change.
- **One conversation per browser tab** (a `session_id` is created on the first message and
  held in Gradio session state; **New chat** clears it).
- **Honest streaming UX.** While a turn runs, a single generic indicator
  (`working on your answer… (Ns)`) is shown — no fabricated per-node labels for activity
  that isn't actually observed. Once the turn's grounding gate (LLD-RT-05) has vetted the
  full draft, the **gated** final text is typewritered word-by-word. Raw LLM tokens are
  never streamed to the UI.
- A collapsed **Sources** panel (deduplicated doc/family names + URLs) below the chat.
- A collapsed **"Dev trace (internal evaluation)"** panel below the chat — the same content
  as `agentkit chat --show-trace` (turn_id, routing, triage tags, tool calls with
  args/row counts, grounding result, prompt versions), clearly separated from the transcript.

The **favicon** lives at `apps/assets/favicon.png` (or `.ico`/`.svg`); drop the Jyotech
icon there and it's picked up automatically. The app still launches without one.

### Setup — each worktree gets its **own** virtualenv

Pythons on this machine are uv-managed. Create this worktree's `.venv` from the same
3.12 interpreter the main checkout uses, and install this checkout editable into it.
**Never `pip install -e` this branch into another checkout's venv** — an editable install
rewires that environment's `agentkit` imports to point at this branch.

```bash
# from this worktree's repo root
/Users/nyalazone/.local/share/uv/python/cpython-3.12.11-macos-aarch64-none/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[ui,dev]'
```

Or, if `uv` is on your PATH (same result, faster installs):

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e '.[ui,dev]'
```

The `ui` extra pins the current stable gradio major (`gradio>=5,<6`).

### Run

Launch from the **repo root** (required for `.env` discovery — pydantic-settings resolves
`env_file=".env"` relative to the current working directory, and the runtime reads
`LLM_BASE_URL` / `EMBED_BASE_URL` / `DATABASE_URL` from `.env`):

```bash
JYOTECH_DEV_UI=1 python -m apps.dev_ui
```

Safety rails (this must never run public-facing):

- **Refuses to start** unless `JYOTECH_DEV_UI=1` is set.
- Binds **`127.0.0.1`** only; `share=False` is hardcoded (no public Gradio link).

It talks to the same self-hosted LLM/embedding servers and Postgres as the CLI. Those may
be busy with golden-suite runs from another session — **do not restart or reconfigure any
server**; just retry if a turn is slow.

### Tests

The adapter layer (`apps/harness.py`) is unit-tested with the turn function mocked (no
gradio, no DB, no live LLM):

```bash
python -m pytest tests/apps/ -q
```
