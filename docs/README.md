# Jyotech Agent — Documentation Process

This folder is the single source of truth for the Jyotech agentic chatbot. Code follows the documents, never the other way round.

## The three tiers

| Tier | File | Owner | Answers |
|---|---|---|---|
| PRD | `PRD.md` | Product (Arnab) | *What* must the product do and *why*. Requirements carry IDs `PRD-F-nnn` (functional) and `PRD-N-nnn` (non-functional). |
| HLD | `HLD.md` | Architecture | *How* the system is shaped to meet the PRD: components, data stores, flows, boundaries. Items carry IDs `HLD-nnn` and cite the PRD IDs they satisfy. |
| LLD | `LLD.md` | Engineering | *Exactly how* each HLD component is built: schemas, tool contracts, agent contracts, extraction schemas, prompts. Items carry IDs `LLD-nnn` and cite the HLD IDs they implement. |

Supporting documents: `design/flow.md` (Mermaid flows, referenced by HLD), `design/data-model.md` / `.html` (referenced by LLD), `TRACEABILITY.md` (PRD → HLD → LLD → code/tests matrix), `CHANGELOG.md` (one entry per document version).

## Versioning rules

1. Every document has a `version` in its front-matter (`MAJOR.MINOR`). MAJOR bumps when scope changes (a requirement is added or removed); MINOR when an existing requirement is clarified or re-prioritised.
2. **Only the PRD is edited by hand.** HLD and LLD are changed *in response to* a PRD change, via a Change Request (CR), and their versions record which PRD version they are aligned to (`aligned_to_prd: 1.2`).
3. Requirement IDs are never reused. A removed requirement stays in the PRD with status `Withdrawn` and the CR that withdrew it.
4. Every CR lives in `changes/CR-nnnn.md` (template: `changes/_TEMPLATE.md`) and records: the PRD diff, the impact analysis (which HLD/LLD IDs change), the resulting document versions, and the code/test changes.
5. A git tag `docs/prd-v1.2` marks each PRD version. `git diff docs/prd-v1.1 docs/prd-v1.2 -- docs/PRD.md` is the authoritative diff for a CR.

## The change flow

```
edit PRD.md  →  open CR  →  impact analysis  →  update HLD  →  update LLD
     │                                                              │
     └── bump PRD version, CHANGELOG, tag                           └── update TRACEABILITY, then code + tests
```

In Claude Code this is driven by two slash commands (see `.claude/commands/`):

- `/prd-change <CR title>` — guides the PRD edit, assigns the next requirement/CR IDs, bumps the version, writes the CR file and CHANGELOG entry.
- `/propagate-prd CR-nnnn` — reads the CR and the PRD diff, proposes the HLD and LLD edits with their new versions, updates TRACEABILITY, and lists the code and tests that must change. It stops for review before touching code.

## Definition of "aligned"

HLD and LLD are aligned when `TRACEABILITY.md` shows every `Active` PRD requirement mapped to at least one HLD item, every HLD item to at least one LLD item, and no HLD/LLD item cites a withdrawn requirement. `/propagate-prd` checks this and refuses to finish while it is false.
