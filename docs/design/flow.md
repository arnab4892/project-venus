# Jyotech Agent — End-to-End Flow (Iteration 1)

Scope for iteration 1: knowledge comes **only** from the public website (≈10 pages) and the two catalogue PDFs (`Jyotech Catalog - PROCESS.pdf`, `Jyotech Catalog - F&S.pdf`). No client-supplied datasheets, price lists or CRM integration yet. Every path that needs something outside that scope ends in a structured human handoff to `sales@jyotech.com` / regional office.

The flow is drawn in four layers, top-down: the overall pipeline, the offline ingestion that builds the knowledge stores, the runtime conversation loop, and the sub-agent / handoff detail. Diagram 3 is the one engineers will build against; diagram 4 shows the two flows that carry most of the business value.

---

## 1. System overview

```mermaid
flowchart LR
    subgraph SRC["Sources (public only)"]
        W["jyotech.com<br/>10 HTML pages"]
        P1["PROCESS catalogue PDF"]
        P2["F&S catalogue PDF"]
    end

    subgraph ING["Offline ingestion (batch, re-runnable)"]
        CR["Crawl & parse"]
        NR["Normalise → Markdown"]
        EX["Extract structured facts"]
        CH["Chunk + embed"]
    end

    subgraph KS["Knowledge stores (read-only at runtime)"]
        CM[("Capability matrix<br/>gas × flow × pressure → family")]
        PR[("Product registry<br/>families, models, kits")]
        DR[("Document registry<br/>pages, PDFs, certs, offices")]
        VS[("pgvector chunks<br/>+ FTS")]
    end

    subgraph RT["Runtime (Python backend, LangGraph)"]
        OR["Orchestrator / triage"]
        AG["Intent sub-agents"]
        TL["Tools"]
    end

    subgraph OPS["Operational store (Postgres)"]
        SE[("sessions · messages<br/>turns · tool calls · leads")]
        PV[("prompt versions<br/>client config")]
    end

    subgraph OUT["Outcomes"]
        ANS["Cited answer in widget"]
        HO["Structured handoff<br/>email → sales@ / regional office"]
    end

    W --> CR
    P1 --> CR
    P2 --> CR
    CR --> NR --> EX --> CM
    EX --> PR
    EX --> DR
    NR --> CH --> VS

    UI["Angular widget<br/>embedded on jyotech.com"] <--> OR
    OR --> AG --> TL
    TL --> CM
    TL --> PR
    TL --> DR
    TL --> VS
    AG --> ANS
    AG --> HO
    OR --> SE
    AG --> SE
    PV --> OR
```

---

## 2. Ingestion pipeline

Runs once per content release. Output is versioned so a bad parse can be rolled back without touching the operational store.

```mermaid
flowchart TD
    A["Seed URLs<br/>index, about, 6 product pages,<br/>catalogue, achievement, contact, career"] --> B["Crawler<br/>(same-host only, follow .html/.php/.pdf)"]
    B --> C{"Content type?"}
    C -->|HTML| D["Strip nav/footer,<br/>keep headings, tables, alt text"]
    C -->|PDF| E["PDF → Markdown<br/>(layout-aware; keep tables,<br/>model numbers, section headings)"]
    D --> F["Normalised Markdown<br/>+ source URL, page/section, fetched_at"]
    E --> F

    F --> G["LLM extraction pass<br/>(schema-constrained)"]
    G --> G1["Capability rows<br/>product_family, gas, type,<br/>lubricated?, cooling,<br/>capacity_min/max, pressure_min/max,<br/>standard, applications, source"]
    G --> G2["Product registry rows<br/>division, category, family,<br/>model/variant, description, source"]
    G --> G3["Document & entity rows<br/>PDF links, ISO certs,<br/>offices, contacts, industries served,<br/>named clients"]
    G1 --> H{"Human review<br/>(one-time, ~40 rows)"}
    G2 --> H
    G3 --> H
    H -->|approved| I[("Fact tables<br/>Postgres, read-only schema")]

    F --> J["Chunk by heading<br/>(~400–600 tokens, overlap,<br/>table rows kept whole)"]
    J --> K["Embed (self-hosted model)"]
    K --> L[("chunk_embedding<br/>pgvector + FTS index")]

    I --> M["Release tag vN"]
    L --> M
    M --> N["Smoke test:<br/>golden questions must still pass"]
    N -->|pass| O["Promote release → runtime config"]
    N -->|fail| G
```

What the extraction pass should yield from the material that exists today: roughly 8 compressor families on the industrial side (oxygen, process reciprocating, natural gas, diaphragm, medium/high-pressure air & gas, CNG/bio-gas boosters, hydrogen fuelling, air separation) with range-level specs; roughly 10 equipment categories on the fire/rescue/diving side with model names (MCH series, lifting bags, SCBA, search cameras, hydraulic tools, hazmat kits, diving sets); plus ISO 9001/14001/45001, four office locations, the industries-served list and the named-clients list.

---

## 3. Runtime conversation flow

```mermaid
flowchart TD
    S0(["Visitor opens widget"]) --> S1["Create session<br/>(cookie, UA, IP, landing page)"]
    S1 --> S2["Greeting + 4 quick-start chips:<br/>Industrial compressors · Fire/Rescue/Diving ·<br/>Service & spares · Documents"]
    S2 --> U["User message"]

    U --> T["Triage agent<br/>classify: division, intent, language,<br/>in-scope?, PII present?"]
    T --> G1{"In scope?"}
    G1 -->|No| OOS["Polite decline +<br/>what I can help with"] --> U
    G1 -->|Yes| G2{"Intent"}

    G2 -->|"Application enquiry<br/>(industrial)"| A1["Application Discovery agent"]
    G2 -->|"Product / kit question<br/>(fire, rescue, diving)"| A2["Product Advisor agent"]
    G2 -->|"Docs, certs, offices,<br/>company facts"| A3["Documents & Compliance agent"]
    G2 -->|"Service, spares, AMC,<br/>existing machine"| A4["After-sales Intake agent"]
    G2 -->|"Price, lead time,<br/>dealer, export"| A5["Commercial Routing agent"]
    G2 -->|"Careers / general"| A6["FAQ & Deflect agent"]

    A1 --> TOOLS
    A2 --> TOOLS
    A3 --> TOOLS
    A4 --> TOOLS
    A5 --> TOOLS
    A6 --> TOOLS

    subgraph TOOLS["Tool layer (read-only fact stores)"]
        direction LR
        t1["match_capability(gas, flow, pressure, ...)"]
        t2["list_products(division, category)"]
        t3["get_product(family | model)"]
        t4["search_documents(query, division)"]
        t5["get_company_fact(kind)"]
        t6["get_office(region)"]
    end

    TOOLS --> V["Grounding check<br/>every claim maps to a retrieved chunk or fact row;<br/>otherwise say 'not published' + offer handoff"]
    V --> R{"Resolved from<br/>public content?"}
    R -->|Yes| ANS["Answer with citations<br/>(page / PDF + section)<br/>+ 'Talk to an engineer' button"]
    R -->|Partially / No| HO["Handoff agent"]

    ANS --> FU{"Follow-up?"}
    FU -->|Yes| U
    FU -->|No| END(["Session idle / close"])

    HO --> L1["Collect: name, company, phone/WhatsApp,<br/>email, city; attach structured enquiry"]
    L1 --> L2{"Consent + minimum<br/>fields present?"}
    L2 -->|No| L1
    L2 -->|Yes| L3["Create lead record (Postgres)"]
    L3 --> L4["Email with transcript summary<br/>(after-sales → branch-office To, sales@ Cc;<br/>else sales@)"]
    L4 --> L5["Confirm to user:<br/>reference no. + expected callback"]
    L5 --> END

    T -. "log turn, intent,<br/>confidence" .-> DB[("Operational store")]
    A1 -. "log sub-agent call,<br/>tool calls" .-> DB
    V -. "log grounding result" .-> DB
    L3 -. "lead" .-> DB
```

Rules that apply across every branch:

- **Escape hatch on every turn.** A "Talk to an engineer" action is always visible; pressing it jumps straight to the Handoff agent with whatever context has been collected.
- **Grounding is a gate, not a suggestion.** If the answer cannot be tied to a chunk or fact row from the current release, the bot says so and offers the handoff. No inference about specs, prices, delivery or warranty.
- **No troubleshooting advice.** After-sales is intake and routing only; high-pressure gas systems are not something a bot should diagnose.
- **Language.** Respond in the user's language (English / Hindi / Hinglish); retrieval stays in English with query translation.

---

## 4. The two high-value sub-flows

### 4a. Application Discovery (industrial compressors)

A guided qualification, not open Q&A. The bot asks only what the capability matrix can actually discriminate on, then either places the duty in a family or hands off.

```mermaid
flowchart TD
    Q0["Entry: user mentions gas, flow,<br/>pressure, plant type or RFQ"] --> Q1["Extract slots already given<br/>gas · flow · suction P · discharge P ·<br/>oil-free? · driver · standard · industry · timeline"]
    Q1 --> Q2{"Enough slots<br/>to match?"}
    Q2 -->|No| Q3["Ask ONE missing slot<br/>(gas → flow → discharge P → oil-free → standard)"]
    Q3 --> Q1
    Q2 -->|Yes| Q4["match_capability()"]
    Q4 --> Q5{"Match result"}
    Q5 -->|"Inside a published range"| Q6["Present family + type + standard,<br/>cite catalogue section,<br/>link PDF; note range not a quote"]
    Q5 -->|"Near edge / ambiguous"| Q7["Present nearest family,<br/>state the published limit explicitly"]
    Q5 -->|"Outside all ranges"| Q8["'Not in our published range'<br/>+ offer engineer handoff"]
    Q6 --> Q9{"Next step?"}
    Q7 --> Q9
    Q9 -->|"Send RFQ / talk to engineer"| H["Handoff with<br/>pre-filled enquiry JSON"]
    Q9 -->|"Compare / another duty"| Q1
    Q9 -->|"Done"| X(["End"])
    Q8 --> H
```

### 4b. After-sales intake (existing customers)

```mermaid
sequenceDiagram
    actor U as Existing customer
    participant W as Widget
    participant O as Orchestrator
    participant A as After-sales Intake agent
    participant F as Fact tools
    participant DB as Operational store
    participant M as Mail relay

    U->>W: "Need spares for MCH-16 in Chennai"
    W->>O: message + session
    O->>A: route (intent = after-sales)
    A->>F: get_product("MCH-16")
    F-->>A: family, division, catalogue ref
    A->>F: get_office("Chennai")
    F-->>A: Chennai office contact
    A-->>U: Confirms model family, asks: serial/year, site, issue or part needed, preferred contact
    U-->>A: details
    A->>DB: create lead (type=after_sales, region=South)
    A->>M: email Chennai office + sales@ with structured ticket
    A-->>U: Reference no., office contact, expected callback window
    A->>DB: log sub-agent call, tool calls, outcome
```

---

## 5. Data-model deltas vs. the C&S framework

| Area | C&S Electric build | Jyotech iteration 1 |
|---|---|---|
| Fact store | `sku_fact` wide table, ~9k SKUs, SQLite | `capability_row`, `product`, `company_fact`, `office` — a few hundred rows, plain Postgres tables |
| Vector store | pgvector per release | same |
| Primary tool | `get_sku`, `compare_skus` | `match_capability` (range matching), `get_product` (family/model level) |
| Lead capture | later phase | **day 1** — `lead` table + mail relay; it is the product |
| Handoff routing | sales only | sales@ + regional office by division/region |
| Sub-agents | 8 (incl. spec-driven SKU selection) | 6, with Application Discovery as the guided-slot flow |

---

## 6. Iteration-1 boundaries (explicit non-goals)

Pricing, lead time, stock, warranty terms, model-level datasheets beyond the catalogue, troubleshooting guidance, CRM/WhatsApp integration, and multi-client tenancy features are out. Each of these has a defined exit in the flow above (handoff), so adding them later is additive, not a redesign.
