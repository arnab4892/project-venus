---
document: HLD
product: Jyotech Agent
version: 1.2
aligned_to_prd: 1.2
status: Approved
date: 2026-08-28
changelog: see CHANGELOG.md
---

# High-Level Design — Jyotech Agent (Iteration 1)

Diagrams: `design/flow.md` §1 (system overview), §2 (ingestion), §3 (runtime), §4 (sub-flows). This document names the components and the decisions; the diagrams show the mechanism.

## 1. Architecture decisions

| ID | Decision | Rationale | Satisfies |
|---|---|---|---|
| HLD-001 | One Postgres database with three schemas: `facts` (read-only, release-tagged), `vec` (per-release embedding tables), `ops` (append-only runtime). | Fact rebuilds never touch history; rollback = flip `release.is_active`. | PRD-F-014, PRD-N-003 |
| HLD-002 | Knowledge is modelled at **product-family** granularity with a **capability matrix** of published ranges, not SKU-level facts. | Public content only states ranges; finer claims would be invented. | PRD-F-003, PRD-F-009 |
| HLD-003 | Lead capture and email dispatch are in scope from day one. | For Jyotech the handoff *is* the product outcome. | PRD-F-011, G-1 |
| HLD-004 | Runtime is a LangGraph orchestrator: triage → one of six intent sub-agents → shared read-only tool layer → grounding gate → answer or handoff. | Intent routing keeps prompts small and testable; the gate enforces PRD-F-009 mechanically. | PRD-F-001…013 |
| HLD-005 | The customer-facing runtime uses a self-hosted open-source LLM (chat) and embedding model behind an internal OpenAI-compatible endpoint; no customer data leaves Nyalazone infrastructure. The offline extractor may use an external API LLM, bounded to public website/catalogue content; the embedding model stays self-hosted (shared runtime query/chunk vectors). | PRD-N-002; runtime same endpoint contract as the C&S build; extraction of public content can use a stronger external model to cut review effort. | PRD-N-002 |
| HLD-006 | Ingestion is a two-stage offline pipeline: layout-aware PDF/HTML → Markdown, then schema-constrained LLM extraction with evidence strings into `staging.*`, human review, promotion to `facts.*` under a new release. The extraction LLM may be external per HLD-005 (public content only); capture, evidence and review are unchanged. | Separates faithful text capture from interpretation; evidence makes review cheap. | PRD-F-014, PRD-F-015 |
| HLD-007 | Widget is an Angular web component loaded by a script tag (not iframe); theme and assets come from `ops.client.theme`. | Script tag allows page-context quick-start chips; iframe reserved for hostile CSPs. Resolves PRD Q-1. | PRD-N-004 |
| HLD-008 | Prompts are versioned rows in `ops.prompt_version`, activated per client; no prompt text in code. | Prompt changes are releases, gated by PRD-N-005. | PRD-N-005 |
| HLD-009 | Golden-question suite runs against facts + retrieval + full agent before activating a release or prompt version. | PRD-N-005. | PRD-N-005, G-2 |

## 2. Components

| ID | Component | Responsibility | Satisfies | Detailed in |
|---|---|---|---|---|
| HLD-C-01 | Crawler & converter | Fetch seed URLs and PDFs; detect scanned pages; emit normalised Markdown with page markers and content hash. | PRD-F-014 | LLD §2 |
| HLD-C-02 | Extractor | Section classifier + four typed extractors (capability, product, company fact, office) → `staging.*` with evidence. | PRD-F-015 | LLD §3 |
| HLD-C-03 | Review & release | Review export, approval, integrity checks, release promotion, rollback. | PRD-F-014, PRD-F-015 | LLD §4 |
| HLD-C-04 | Chunker & embedder | Heading-based chunks tagged with family_ids; per-release pgvector + FTS. | PRD-F-008 | LLD §5 |
| HLD-C-05 | Tool layer | Six read-only tools over `facts.*`/`vec.*`. | PRD-F-003…005 | LLD §6 |
| HLD-C-06 | Orchestrator | Session, triage, routing, grounding gate, logging to `ops.*`. | PRD-F-001, F-008, F-009, N-003 | LLD §7 |
| HLD-C-07 | Intent sub-agents | Application Discovery, Product Advisor, Documents & Compliance, After-sales Intake, Commercial Routing, FAQ & Deflect. | PRD-F-002…007, F-013 | LLD §8 |
| HLD-C-08 | Handoff service | Contact collection, consent, lead creation, reference number, email dispatch, region routing to the published branch-office email for the region (fallback sales@). | PRD-F-006, F-010, F-011 | LLD §9 |
| HLD-C-09 | Widget | Angular web component; message kinds; quick-start chips; "Talk to an engineer"; language. | PRD-F-010, F-012, N-004 | LLD §10 |
| HLD-C-10 | Eval harness | Golden questions; release/prompt activation gate. | PRD-N-005 | LLD §11 |

## 3. Data stores

| Store | Schema | Written by | Read by |
|---|---|---|---|
| Fact store | `facts.*` | HLD-C-03 (promotion only) | HLD-C-05 |
| Vector store | `vec.chunk_embedding_<release>` | HLD-C-04 | HLD-C-05 |
| Staging | `staging.*` | HLD-C-02 | HLD-C-03 |
| Operational | `ops.*` | HLD-C-06, C-07, C-08 | Widget (history), eval, analytics |

Full table definitions: `design/data-model.md`.

## 4. Key flows

- **Content release** (`design/flow.md` §2): sources → C-01 → C-02 → C-03 review → C-04 → golden questions (C-10) → activate.
- **Conversation** (`design/flow.md` §3): widget → C-06 triage → C-07 agent → C-05 tools → grounding gate → reply (1..n messages) or C-08 handoff.
- **Application discovery** (`flow.md` §4a) and **after-sales intake** (§4b) are the two flows that carry G-1 and G-3.

## 5. Boundaries and non-functional design

- Latency (PRD-N-001): one LLM call for triage, one for the sub-agent, tool calls are SQL; grounding check is a cheap second pass on the draft. Target budget: triage 0.4 s, agent 2.5 s, tools < 0.1 s, grounding 0.8 s.
- Privacy (PRD-N-003): contact block is written to `ops.lead` only after `consent_at`; `ops.session.ip` retained; no PII in prompts beyond the current session.
- Data egress (PRD-N-002): the customer-facing runtime is self-hosted and no customer/conversation data leaves Nyalazone infrastructure; the only permitted external call is the offline extractor's LLM, bounded to public website/catalogue content (no customer data).
- Multi-client (PRD-N-004): all Jyotech specifics live in `ops.client`, `ops.prompt_version`, the extraction schema registry entry `clients/jyotech/`, and content. Framework code is client-agnostic.
- Availability (PRD-N-006): stateless API behind a reverse proxy; Postgres with daily backups; LLM endpoint health-checked, degraded mode returns "temporarily unavailable, leave your details" (handoff form still works without the LLM).

## 6. Revision history

| Version | Date | CR | Aligned to PRD | Summary |
|---|---|---|---|---|
| 1.2 | 2026-08-28 | CR-0002 | 1.2 | HLD-005 scoped: runtime self-hosted (no customer-data egress); the offline extractor may use an external API LLM on public content, embeddings self-hosted. HLD-006 and §5 data-egress notes added. |
| 1.1 | 2026-08-23 | CR-0001 | 1.1 | HLD-C-08 region routing targets the published branch-office email for the region (fallback sales@); reflects the PRD-F-006 modify. |
| 1.0 | 2026-08-23 | — | 1.0 | Initial HLD |
