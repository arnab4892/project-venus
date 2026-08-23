# Jyotech Agent — working rules for Claude Code

## What this is
Customer-facing agentic chatbot for Jyotech Engineering (jyotech.com), built on Nyalazone's reusable RAG-agent framework. Iteration 1 uses only public website content and two catalogue PDFs.

## Documents are the source of truth
- `docs/PRD.md` (what/why) → `docs/HLD.md` (shape) → `docs/LLD.md` (exact build). Read the relevant LLD section before writing code for it, and cite the LLD id in the commit message (e.g. `feat(tools): match_capability [LLD-TOOL-01]`).
- `docs/design/flow.md` and `docs/design/data-model.md` are referenced by HLD/LLD and are authoritative for flows and schemas.
- **Never edit HLD.md or LLD.md directly.** A design change starts as a PRD change: run `/prd-change`, then `/propagate-prd`. If you believe the code needs something the LLD doesn't specify, stop and say so — do not silently extend the design.
- If you find an inconsistency between docs and code, report it; the docs win unless the user decides otherwise.

## Stack and conventions
- Python 3.12, FastAPI, LangGraph, SQLAlchemy + Alembic, Postgres 16 + pgvector, Pydantic v2, Docling. Angular 18 web component for the widget.
- LLM / embeddings via OpenAI-compatible endpoints `LLM_BASE_URL`, `EMBED_BASE_URL` (self-hosted). Secrets in `.env` (see `.env.example`), never in code or chat.
- `facts.*` is read-only at runtime and written only by `release promote`. `ops.*` is append-only. Every fact row has `source_doc_id` + `source_locator`. No LLM output is written to `facts.*` without passing through `staging.*` and review.
- Client-specific things live only in `clients/<client_id>/` and in `ops.client` / `ops.prompt_version`. Framework code under `src/agentkit/` must not mention Jyotech.
- Tests: `pytest -q`; golden suite: `agentkit eval run jyotech`. Write the test named in `docs/TRACEABILITY.md` before the feature.

## How to work
- Plan first for any task touching more than one module; show the plan, wait for approval.
- Keep commits small, one LLD id each where possible. Run `pytest -q` before claiming done.
- After finishing an LLD item, update the `Code` / `Tests` columns in `docs/TRACEABILITY.md` for that row.
- Build order (milestones): DB migration + seed → ingestion stage 1 (show Markdown for review) → extraction to staging + review export → promote + embed + tools + golden tests → orchestrator + agents + grounding (trace the hydrogen conversation from data-model.md §4) → handoff + email → widget.
