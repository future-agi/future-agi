# Seed Agent Templates — Orchestration-Layer Use-Case Catalog & Build Plan

**Goal.** Seed a curated set of production-grade **voice and chat agents** on the FutureAGI platform so
users get a head start: they clone a template whose *agent workflow* (branches, tools, guardrails) and
*simulation environment* are already built. Each seed is authored as a **LiveKit Agents** app (Python),
hosted on GitHub, and registered on the platform as an `AgentDefinition` (provider = `livekit`) that the
Simulate stack runs scenarios against.

**What a seed is (per the locked scope).** Not a single system prompt — a **multi-agent workflow** that
orchestrates phases/branches on demand, with **full function-tool definitions**, guardrails, escalation,
and end conditions. Depth over breadth.

---

## 1. Method & sources

Scanned the published templates, docs, and example repos of nine orchestration layers (Sept 2026):

| Layer | Type | Primary source |
|---|---|---|
| **LiveKit Agents** | OSS framework — **our build target** | github.com/livekit-examples, docs.livekit.io/agents, github.com/livekit/agents/tree/main/examples |
| Vapi | Voice (Assistants / Squads) | docs.vapi.ai/guides, /squads, github.com/VapiAI/examples |
| Retell AI | Voice + chat (Conversation Flow) | docs.retellai.com/build/conversation-flow, retellai.com/blog/conversational-ai-examples |
| Bland AI | Voice (Conversational Pathways) | docs.bland.ai/tutorials/pathways, university.bland.ai |
| Synthflow / Play.ai / Vocode | Voice (no-code + OSS) | synthflow.ai, docs.play.ai, github.com/vocodedev/vocode-core |
| Sierra | Chat + voice (Agent SDK / Skills) | sierra.ai/product, /customers/sonos |
| Intercom Fin | Chat + voice (Procedures / Data connectors) | fin.ai, intercom.com/help |
| Decagon | Chat + voice (Agent Operating Procedures) | decagon.ai/product/aop, case-studies |
| Voiceflow / Botpress | Chat + voice (Playbook / Autonomous Node) | voiceflow.com/docs, botpress.com/docs |

Node-type vocabularies and use-case catalogs are verified from official docs. Exact tool signatures are
rarely published verbatim (they live behind dashboards); where a signature is shown below it is a
normalization of a *documented capability*, not a quoted config.

---

## 2. Cross-platform use-case catalog

Every distinct use case these layers ship, with how common it is and which layers offer it. Commonality
is the single strongest signal for what to seed.

| Use case | Vertical | Channel | Ships on | Commonality |
|---|---|---|---|---|
| **Appointment scheduling / setter** | Cross (health, SMB, real estate) | Voice + chat | Vapi, Retell, Bland, Synthflow, Play.ai, LiveKit (frontdesk), Voiceflow, Botpress | ★★★★★ |
| **Inbound receptionist / answering service** | SMB / cross | Voice | Vapi, Retell, Bland, Synthflow, Play.ai, LiveKit (frontdesk) | ★★★★★ |
| **Customer support / FAQ deflection** | Cross | Voice + chat | All nine | ★★★★★ |
| **Lead qualification / outbound SDR** | Sales | Voice + chat | Vapi, Retell, Bland, Synthflow, Voiceflow, Botpress | ★★★★★ |
| **Order status / WISMO + returns/exchanges** | E-commerce / retail | Chat + voice | Vapi (ecommerce squad), Retell (WISMO), Sierra, Fin, Decagon, Voiceflow, Botpress | ★★★★★ |
| **Healthcare patient intake + triage** | Healthcare | Voice | Vapi (medical triage), Retell, Bland, LiveKit (medical_office_triage, healthcare), Synthflow | ★★★★☆ |
| **Insurance FNOL / claims intake + policy** | Insurance | Voice + chat | Retell (Matic FNOL), Bland, Synthflow, Fin (policy updates) | ★★★★☆ |
| **Debt / payment collections** | Financial services | Voice (outbound) | Retell (Medical Data Systems), Bland, Synthflow | ★★★★☆ |
| **Banking / fintech support (card, fraud, disputes)** | Fintech | Chat + voice | Fin (fintech pack), Decagon (fintech), Retell (Sunshine Loans), Synthflow | ★★★★☆ |
| **Restaurant reservation / QSR ordering** | Hospitality | Voice | Bland (restaurant template), LiveKit (restaurant, drive_thru) | ★★★★☆ |
| **Subscription management (cancel/downgrade/retention)** | SaaS / consumer | Chat | Sierra (WeightWatchers), Decagon (ClassPass), Fin | ★★★☆☆ |
| **Technical troubleshooting** | Consumer tech / SaaS | Chat + voice | Sierra (Sonos), Fin (SaaS), Decagon | ★★★☆☆ |
| **IT / internal service desk (password, VPN, access)** | Internal ops | Chat | Retell (Everise), Botpress, Voiceflow | ★★★☆☆ |
| **Identity verification (as a shared sub-flow)** | Cross | Chat + voice | Fin, Decagon, Sierra, Synthflow | ★★★☆☆ |
| **Outbound survey / feedback collection** | Cross | Voice | Retell, LiveKit (survey_caller), Vapi | ★★★☆☆ |
| **Product finder / personal shopper** | E-commerce | Chat + voice | Voiceflow, LiveKit (personal_shopper), Sierra | ★★★☆☆ |
| **Warranty / claims submission** | Retail / insurance | Chat | Sierra, Fin | ★★☆☆☆ |
| **Real-estate lead follow-up / property inquiry** | Real estate | Voice + chat | Bland, Synthflow, Voiceflow, Air.ai | ★★☆☆☆ |
| **Warm transfer to human (as a shared capability)** | Cross | Voice | LiveKit (warm-transfer), Vapi, Retell, Bland | ★★★☆☆ |
| **IVR navigation / phone-tree traversal** | Cross | Voice | LiveKit (IVR navigator), Vocode (DTMF) | ★★☆☆☆ |

### 2.1 Orchestration/branching vocabulary — how each layer models a workflow

This matters because our seeds must reproduce these branch patterns in LiveKit. They converge:

| Layer | Workflow primitive | Branch mechanism | Human handoff | Data/tool call |
|---|---|---|---|---|
| **LiveKit** | `Agent` subclasses + `Task`s on one `AgentSession` | tool returns **next `Agent`** (handoff); `ToolError` re-prompts | `warm-transfer` / SIP REFER | `@function_tool` (docstring=desc, type hints=schema) |
| Vapi | Assistants / **Squads** | AI or logical **edge conditions**; **Global node** ("speak to human") | Transfer Call / squad-to-human | API Request node, function tools, cal.com |
| Retell | **Conversation Flow** | transition conditions + default fallback; **Logic node** | Agent Transfer (warm) | **Function node** (deterministic), Subagent node tools, calendar/SMS |
| Bland | **Conversational Pathways** | edge conditions gated by **extracted variables**; **loop-until-complete**; **Global nodes** auto-return | Transfer Call / Transfer Chat | **Webhook node** (POST vars → response vars) |
| Sierra | **Skills + Procedures/Plans** | declarative goals + deterministic **guardrails**; **supervisor agents** | summary + context to rep | 40+ integrations, Salesforce |
| Fin | **Procedures** (NL) + Workflows | branching instruction blocks, sub-procedures | model-based + hard-coded high-risk auto-handoff | **Data connector** = one configurable API call |
| Decagon | **Agent Operating Procedures** (NL + code) | two-layer decisioning; **sensitive validation in code** | rich-context escalation, configurable resume | actions + KB integrations |
| Voiceflow | **Agent/Playbook step** (+ Capture/Intent) | exit conditions; Condition/Operator | **Agent Handoff step** (Genesys, Zendesk…) | API / Function / MCP tools |
| Botpress | **Autonomous Node** vs Standard Node | loops until exit; `workflow.transition` | `hitl.startHitl` → ticket | Execute Code (Axios), Tables, `global.search` |

**The five recurring patterns every seed should implement:**
1. **Loop-until-complete data capture** (Bland's "must get date, time, guests" node) → LiveKit `Task` that
   re-prompts until a valid value is captured; `ToolError` on invalid input.
2. **Variable-gated conditional branch** (party size < 8 vs > 8) → a `@function_tool` that inspects
   `userdata` and returns the next `Agent`.
3. **Global pattern-interrupt** (FAQ / "speak to a human" from anywhere) → an always-available tool +
   KB/RAG lookup that answers then resumes.
4. **Deterministic sensitive step** (refunds, identity, payment) → validation in Python, not left to the
   LLM (Decagon's core principle).
5. **Warm human handoff with summary** → `userdata.summarize()` injected into the transfer context.

---

## 3. The curated seed set (v1) — trimmed to the 4 highest-value

Selected where **prod grounding** (debt collection #1, insurance top-4, per the scenario-library research),
**cross-platform commonality**, and an **existing LiveKit example to model on** all intersect.

| # | Seed agent | Channel | Direction | Prod vertical | Competitor coverage | LiveKit pattern to model on |
|---|---|---|---|---|---|---|
| 01 | **Debt Collection / Payment Reminder** | Voice | Outbound | **#1 vertical** | Retell (Med Data Sys), Bland, Synthflow | net-new (compliance state machine) |
| 02 | **Insurance FNOL / Claims Intake + Renewal** | Voice + chat | Inbound + outbound | Top-4 | Retell (Matic), Bland, Fin, Cognigy | frontdesk + form-agent |
| 03 | **Healthcare Scheduling + Patient Intake + Triage** | Voice | Inbound | High | Vapi, Retell, Bland, Synthflow, Parloa | `medical_office_triage`, `frontdesk`, `healthcare` |
| 04 | **Banking / Fintech Support (card, fraud, disputes)** | Voice + chat | Inbound | High (fintech) | Fin, Decagon, Retell, Cognigy | `medical_office_triage` routing + identity gate |

**Deferred (documented, not built now):** restaurant/QSR ordering, e-commerce WISMO+returns, lead-qual/SDR,
subscription retention, IT service desk, outbound survey. Identity verification and warm-transfer are built
as **shared helpers** reused across 02/04, not standalone seeds.

> **Format note.** Seeds are authored as **LiveKit-native** agent definitions (a `config.json` prompt
> workflow + tools that maps 1:1 to LiveKit `AgentSession`/`Agent`/`@function_tool`, plus a runnable
> `agent.py`) — see [`SCHEMA.md`](SCHEMA.md) and [`README.md`](README.md). They are **not** wrapped in the
> FutureAGI platform's create-agent envelope. The simulation stack generates scenarios separately, so seeds
> carry no verifier data.

---

## 4. Seed format & build plan (LiveKit-native)

Full format in [`SCHEMA.md`](SCHEMA.md); project layout in [`README.md`](README.md). In brief, each seed is:

- a **`config.json`** — the prompt workflow + tools, a self-contained LiveKit agent definition
  (runtime STT/LLM/TTS/VAD · typed `session_state` · a `workflow` of phase-agents with handoffs +
  global pattern-interrupts · `tools[]` with JSON-Schema params, mocks, and `ToolError` cases · guardrails).
  It maps 1:1 onto LiveKit `AgentSession` / `Agent` / `@function_tool` / handoffs-return-next-Agent.
- a runnable **`agent.py`** — the LiveKit worker, hand-written per agent so the deterministic guardrails are
  explicit in code (a generic `config.json` → LiveKit loader is a possible future addition).

No FutureAGI-platform envelope, and **no verifier/scenario data** — the simulation stack generates scenarios
against the running agent. **Language:** Python (`livekit-agents`), the canonical SDK. **Hosting:** GitHub;
tool backends are mocked inline so an agent runs standalone, pointed at real endpoints for production.

**Done when:** every branch and terminal disposition is enumerated (no "…etc."); every tool has params, a
mock, and its `ToolError` paths; guardrails name the steps enforced in code, not by the model; and
`agent.py` imports/compiles as a LiveKit worker.

---

## 5. Notes & flags

- **Air.ai** excluded as a reference: its technical claims are marketing-only and it faced FTC enforcement.
- No-code layers (Synthflow, Play.ai) don't publish tool schemas — used only for use-case breadth.
- Debt-collection compliance (FDCPA/TCPA, mini-Miranda, opt-out) is inferred from marketing + regulation,
  not a published pathway — the exemplar encodes it explicitly and flags it for legal review before use.
- Prod-grounding figures (debt collection #1, insurance top-4) come from prior scenario-library research
  and should be re-confirmed against current prod before final vertical lock.
