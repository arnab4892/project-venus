---
document: PRD
product: Jyotech Agent
version: 1.0
status: Approved
date: 2026-08-23
owner: Arnab Sharma (Nyalazone)
changelog: see CHANGELOG.md
---

# Product Requirements — Jyotech Agent (Iteration 1)

## 1. Purpose

Jyotech Engineering (jyotech.com, Noida) sells industrial gas compressors, CNG/hydrogen fuelling systems, breathing-air compressors, fire/rescue and diving equipment to PSUs, refineries, CGD companies, fire services, defence and disaster-management agencies. The website is thin (≈10 pages, 2 catalogue PDFs) and offers no way for a visitor to find out quickly whether Jyotech can meet a duty, or to reach the right person.

The Jyotech Agent is a customer-facing chatbot embedded on jyotech.com that qualifies enquiries against Jyotech's published capability, answers product and company questions with citations, and routes every enquiry that needs a human to the right inbox with a structured, pre-qualified summary.

## 2. Goals and success metrics

| ID | Goal | Metric (first 90 days) |
|---|---|---|
| G-1 | Turn visitors into qualified enquiries | ≥ 60 % of handoffs arrive at sales@ with a complete enquiry block (all mandatory slots filled) |
| G-2 | Never misstate a specification | 0 answers containing a spec not traceable to a published source, measured on the golden-question set and a weekly 50-conversation audit |
| G-3 | Route after-sales correctly | ≥ 90 % of after-sales handoffs carry the correct region |
| G-4 | Respond fast | p95 turn latency ≤ 6 s |

## 3. Users

| Persona | Primary need | Priority |
|---|---|---|
| P-1 Plant / project engineer (PSU, refinery, steel, CGD) | Can Jyotech do *this* duty, to which standard; get an RFQ moving | Must |
| P-2 Tender / procurement officer | Certificates, catalogues, company facts, office presence | Must |
| P-3 Fire service / disaster-management / diving buyer | Model names, kit contents, which compressor for a station | Must |
| P-4 Existing customer needing service, spares or AMC | Fast route to the regional office, a reference number | Must |
| P-5 Dealer / EPC / international buyer | Distribution and export enquiries routed to sales | Should |
| P-6 Job seeker | Polite deflection to careers page | Could |

## 4. Scope — Iteration 1

**In:** public website text; the two catalogue PDFs (PROCESS, F&S); English, Hindi and Hinglish input; email handoff to sales@ with region in subject; embedded widget on jyotech.com.

**Out (explicit non-goals, each has a handoff exit):** pricing, lead time, stock, warranty terms, model-level datasheets beyond the catalogue, troubleshooting guidance, CRM/WhatsApp integration, client self-service content updates, multi-client tenancy UI.

## 5. Functional requirements

| ID | Requirement | Persona | Priority | Status |
|---|---|---|---|---|
| PRD-F-001 | The agent shall classify every message by division (industrial / fire_rescue / diving), intent, language and in-scope flag before answering. | all | Must | Active |
| PRD-F-002 | For industrial compressor enquiries the agent shall run a guided qualification (gas → flow → discharge pressure → oil-free → standard → industry → timeline), asking for one missing item at a time. | P-1 | Must | Active |
| PRD-F-003 | The agent shall place a stated duty inside, near the edge of, or outside Jyotech's *published* capability ranges and say which, citing the catalogue section. | P-1 | Must | Active |
| PRD-F-004 | The agent shall answer fire/rescue/diving product questions using exact printed model names and only printed attributes. | P-3 | Must | Active |
| PRD-F-005 | The agent shall answer company, certification, office and document questions and serve catalogue/certificate links. | P-2 | Must | Active |
| PRD-F-006 | For service / spares / AMC requests the agent shall collect model, serial/year, site, need and contact, and route to the regional office for the customer's state, copying sales@. It shall not give troubleshooting advice. | P-4 | Must | Active |
| PRD-F-007 | Price, lead-time, dealer, export and any out-of-scope commercial question shall be routed to sales@ with the enquiry context, never answered. | P-1, P-5 | Must | Active |
| PRD-F-008 | Every answer containing a fact shall carry a citation to the page or PDF section it came from. | all | Must | Active |
| PRD-F-009 | If a claim cannot be grounded in the active content release the agent shall say it is not published and offer a handoff, rather than infer. | all | Must | Active |
| PRD-F-010 | A "Talk to an engineer" action shall be visible on every turn and jump directly to handoff with context collected so far. | all | Must | Active |
| PRD-F-011 | Handoff shall collect name, company, phone (WhatsApp opt-in), email, city; require consent; create a lead with a user-visible reference number; email the structured enquiry and a transcript summary. | all | Must | Active |
| PRD-F-012 | The agent shall respond in the user's language (English, Hindi, Hinglish). | all | Should | Active |
| PRD-F-013 | Career and general questions shall be deflected to the relevant page. | P-6 | Could | Active |
| PRD-F-014 | Content shall be ingested from the website and PDFs into a reviewed, versioned fact store and a vector store; a release can be rolled back. | ops | Must | Active |
| PRD-F-015 | Every extracted fact shall carry evidence text and a source locator, and be human-approved before it is used. | ops | Must | Active |

## 6. Non-functional requirements

| ID | Requirement | Priority | Status |
|---|---|---|---|
| PRD-N-001 | p95 turn latency ≤ 6 s; p50 ≤ 3 s. | Must | Active |
| PRD-N-002 | Self-hosted open-source LLM and embedding model; no customer data leaves Nyalazone infrastructure. | Must | Active |
| PRD-N-003 | Full conversation, agent and tool logs retained; PII (contact block) stored only after consent. | Must | Active |
| PRD-N-004 | Widget theme and assets configurable per client; no Jyotech-specific code in the framework core. | Must | Active |
| PRD-N-005 | A golden-question regression suite must pass before any content release or prompt version is activated. | Must | Active |
| PRD-N-006 | Availability 99.5 % monthly for the widget endpoint. | Should | Active |

## 7. Assumptions and open questions

- A-1 Regional office emails are not printed publicly; after-sales routes to sales@ with region in the subject until Jyotech supplies them.
- A-2 The MCH, Neptune, ProEye, Vega/Nova names are treated as product names as printed; whether they are Jyotech-manufactured or represented is to be confirmed with Jyotech (affects warranty/lead-time wording only, all out of scope).
- Q-1 Embed mode: script tag vs iframe — decided in HLD.

## 8. Revision history

| Version | Date | CR | Summary |
|---|---|---|---|
| 1.0 | 2026-08-23 | — | Initial approved PRD for Iteration 1 |
