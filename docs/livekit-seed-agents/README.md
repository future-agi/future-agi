# FutureAGI Seed Agents — LiveKit

A curated set of production-shaped **voice & chat agents** built on [LiveKit Agents](https://docs.livekit.io/agents/),
meant to be forked/hosted on GitHub and stood up on LiveKit directly. Each agent is realistic to what
customers actually run on Vapi / Retell / Bland today — a multi-phase **agent workflow** (branches +
handoffs) with full **tool calls**, not a single prompt.

The FutureAGI simulation stack generates test scenarios against these agents separately — seeds contain no
scenario/verifier data.

## Layout

```
docs/livekit-seed-agents/
  README.md
  requirements.txt
  SCHEMA.md                      # the LiveKit-native config format + code mapping
  SEED_AGENTS_CATALOG.md         # competitive scan across 9 orchestration layers + why these agents
  debt_collection/               # each agent is a self-contained folder:
    config.json                  #   prompt workflow + tools (declarative definition)
    agent.py                     #   runnable LiveKit worker
    README.md                    #   what it does, workflow, guardrails, run steps
  insurance_fnol/
  healthcare_scheduling/
  banking_support/
```

Each agent ships **both** `config.json` (the declarative prompt-workflow + tools) and a runnable `agent.py`.
The config is the source of truth an engineer reads/edits; the `agent.py` is the LiveKit worker built from
it. A generic `config.json` → LiveKit loader is a possible future addition; today each `agent.py` is
hand-written so the deterministic guardrails are explicit.

## The four seed agents (v1)

| Agent | Channel | Direction | Why it's here |
|---|---|---|---|
| **Debt Collection / Payment Reminder** | Voice | Outbound | #1 identifiable prod vertical; compliance-gated workflow |
| **Insurance FNOL / Claims Intake** | Voice + chat | Inbound + outbound | Top-4 prod vertical; Retell/Matic flagship |
| **Healthcare Scheduling + Intake + Triage** | Voice | Inbound | Ubiquitous; LiveKit has close reference examples |
| **Banking / Fintech Support** | Voice + chat | Inbound | High-value; identity-gated, action-taking |

## Run an agent

```bash
pip install -r requirements.txt
# LiveKit + provider credentials in env (LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET,
# OPENAI_API_KEY, DEEPGRAM_API_KEY, CARTESIA_API_KEY)
cd debt_collection && python agent.py dev     # or insurance_fnol / healthcare_scheduling / banking_support
```

Tool backends are mocked inline so an agent runs standalone; point them at real endpoints
(`{{FAI_TOOL_BASE}}`, SIP targets) for production. Compliance-sensitive agents (collections, insurance,
banking) carry engineering-scaffold guardrails, **not legal advice** — review with counsel before live use.
