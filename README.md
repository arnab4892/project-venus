# Jyotech Agent

Customer-facing agentic chatbot for Jyotech Engineering, built on Nyalazone's
reusable RAG-agent framework (`agentkit`). See `docs/` for the PRD → HLD → LLD
chain; `docs/design/` holds the authoritative flows and data model.

## Quick start (local skeleton)

Requires Docker and Python 3.12.

```bash
# 1. Postgres 16 + pgvector on host port 5433 (non-standard, avoids clashes)
docker compose up -d db

# 2. Install the framework + dev tools
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 3. Point at your local DB (defaults already match docker-compose)
cp .env.example .env

# 4. Run migrations — creates the vector extension + facts/vec/staging/ops schemas
agentkit db upgrade
agentkit db current
```

At this milestone the database holds only the four (empty) schemas and the
`vector` extension; the tables from `docs/design/data-model.md` arrive with
migration `0001_init`.

### CLI

```
agentkit db upgrade [--revision head]   # apply migrations
agentkit db downgrade <revision>        # revert (e.g. `base`)
agentkit db current                     # show applied revision
```

### Tests

```bash
pytest -q
```
