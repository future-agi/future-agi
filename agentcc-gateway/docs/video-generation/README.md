# Video generation (TH-8088) — design documents

Status: **proposed design, implementation in progress on branch `feat/TH-8088-gateway-video-generation`**. Nothing in this folder is a statement of deployed or smoke-verified behavior. The verified support matrix lives in the gateway README once each provider passes the contract suite and (where authorized) a live smoke test.

| Document | Purpose |
|---|---|
| [architecture.md](architecture.md) | ADR: durable Redis-backed job lifecycle, API surface, state machine, crash windows, budget/usage, security, rollout |
| [provider-adapter-contract.md](provider-adapter-contract.md) | Go adapter contract and per-provider notes (Seedance in full; cohorts with fail-closed gates) |
| [test-architecture.md](test-architecture.md) | Acceptance IDs AT01–AT32 mapped to test layers; LIVE-only list |
| [implementation-plan.md](implementation-plan.md) | Ordered bounded steps P1-01…P1-15 and cohort plans L06–L10 |
| [sources.md](sources.md) | Evidence ledger for provider facts (checked dates) |
| [diagrams/](diagrams/) | Component/sequence/state Mermaid sources and the rendered SVG overview |

Product requirements (PRD r2, decision log, provider matrix) are in the company brain under `engineering/gateway-video-generation/` (company-brain PR #47).

Hard stops reserved for the product owner: managed tariff / credits charging for video, any paid smoke run or provider account activation, supplier default-public output exposure exceptions (fal/PixVerse), and Nova Reel AWS account/bucket/billing.

Developer and operator pages (as designed, not yet verified against a running build):

| Page | Purpose |
|---|---|
| [api-reference.md](api-reference.md) | `/v1/videos` contract: submit, status, content, list, cancel, local delete, error codes, idempotency |
| [configuration.md](configuration.md) | `video:` config block, BytePlus/ModelArk setup, credentials, Redis prerequisites, artifact storage |
| [operator-runbook.md](operator-runbook.md) | Levers, rollout/rollback, alerts, unresolved submissions, unsettled reservations, Redis outage |
| [implementation-pins.md](implementation-pins.md) | Rick's binding pins N1–N10 / C1–C3 taken after the docs stage |
