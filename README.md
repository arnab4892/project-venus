# Jyotech Agent

Customer-facing agentic chatbot for Jyotech Engineering (jyotech.com), built on Nyalazone's reusable RAG-agent framework. Design documents in `docs/` are the source of truth — start with `docs/README.md` for the PRD → HLD → LLD process.

## Setup

Requirements: Python 3.12, Docker (Postgres 16 + pgvector via `docker-compose.yml`), Ollama serving the chat and embedding models, and **poppler** for the ingestion verifier (`agentkit ingest verify` shells out to `pdftotext`):

```bash
brew install poppler          # macOS
# apt install poppler-utils   # Linux / CI
```

```bash
cp .env.example .env          # fill in endpoints; never commit .env
docker compose up -d
pip install -e ".[dev]"
agentkit db upgrade
agentkit seed demo
pytest -q
```

## Everyday commands

```bash
agentkit ingest run jyotech       # crawl + convert website/PDFs to data/jyotech/md/
agentkit ingest verify jyotech    # token-witness verification (requires poppler)
agentkit tools match-capability --gas hydrogen --capacity 3000 --unit Nm3/hr --discharge-p 350 --oil-free
```

Converted Markdown, raw source bytes and the ingest manifest live under `data/` (gitignored). Never hand-edit files in `data/` — fix the converter or a patch in `clients/jyotech/patches/` and re-run.
