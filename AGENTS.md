# AGENTS.md — future-agi

Typed structured definitions live in [`project.faf`](project.faf) (`application/vnd.faf+yaml`), not in this prose. This file is the briefing; `project.faf` is the source it is authored from. The two are maintained together — update both when the stack or layout changes (`faf-cli` can re-author this file from `project.faf`; nothing is added to dependencies or the build).

Human source of truth stays [README.md](README.md), [CONTRIBUTING.md](CONTRIBUTING.md), [TESTING.md](TESTING.md), [INSTALLATION.md](INSTALLATION.md). This file distils them for agents; it does not replace them.

## What this project is

Open-source, end-to-end platform for evaluating, observing and improving LLM and AI-agent apps — OpenTelemetry tracing, LLM-as-judge and code evaluators, agent simulation, real-time guardrails, and an OpenAI-compatible gateway — on one platform and one feedback loop. Self-hostable (Apache-2.0) or managed Future AGI Cloud. Polyglot monorepo: Python (Django), JavaScript (React), Go.

## Where things live

| Path                | Role                                                                                                       |
| ------------------- | -------------------------------------------------------------------------------------------------------- |
| `futureagi/`        | Django 5.1 + DRF backend (Python ≥3.11) — `tracer/`, `agentic_eval/`, `simulate/`, `accounts/`, `model_hub/`, `mcp_server/`, `tfc/` (settings) |
| `frontend/`         | React 18 + Vite 5 (JavaScript, ESM) — MUI v5, TanStack Query, Zustand                                    |
| `agentcc-gateway/`  | Go — OpenAI-compatible LLM gateway ("Agent Command Center")                                              |
| `fi-collector/`     | Go — OTLP → ClickHouse span collector                                                                    |
| `e2e/`              | Playwright full-stack flows (`bin/e2e`)                                                                  |
| `.agents/skills/`   | Repo agent skills — `writing-e2e-flows`, `reviewing-prs` (read these before those tasks)                 |

Framework instrumentors live in the separate `future-agi/traceAI` repo. Evaluator reference docs live in the docs repo.

## Setup

```bash
cp futureagi/.env.example futureagi/.env
docker compose up -d          # or: ./bin/install  (copies .env, boots the stack, waits for health)
yarn install                  # repo root — installs husky git hooks + lint-staged
cd futureagi && make pre-commit-install   # Python hooks: Black, isort, Ruff, mypy, Django checks
```

Backend: `http://localhost:8000`. Frontend: `http://localhost:3031`.

## Tests

```bash
cd futureagi && make test     # backend — pytest in an isolated Docker Compose stack
cd frontend && yarn test      # frontend — Vitest + React Testing Library
```

Before requesting review: `make check-all` (backend) or `yarn check-all` (frontend). End-to-end: `bin/e2e up && bin/e2e test` (cold first boot ~8–9 min). Full workflow, CI matrix and coverage thresholds: [TESTING.md](TESTING.md).

## Conventions

- **Python:** Ruff + Black (line length 88), isort (Black profile), mypy on new code (baseline-driven — CI rejects new type errors). Obey `futureagi/pyproject.toml` and `futureagi/.pre-commit-config.yaml`.
- **JS:** ESLint (Airbnb) + Prettier. Obey the `frontend/` configs. No `tsconfig.json` today — `yarn type-check` is a no-op until someone adds TypeScript.
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/) — `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`, `perf:`.
- **Branches:** `type/short-description` (or `type/TICKET-short-description`), branched from `dev`. Validated on `git push` — see [BRANCH_NAMING_CONVENTION.md](BRANCH_NAMING_CONVENTION.md).
- **PRs:** base from **`dev`**, not `main`. Keep the diff focused, add tests (every bug fix needs a regression test), explain *what* and *why*. Sign the CLA when the bot prompts on your first PR. A maintainer reviews within 3 business days.

Every evaluator lives under `futureagi/agentic_eval/core_evals/fi_evals/` with a class, a rubric prompt (if LLM-judge), a registration in `eval_type.py`, and tests. No hardcoded secrets, URLs, or PII.

## Out of scope for this repo

- Framework instrumentors → `future-agi/traceAI`
- Evaluator reference docs → the docs repo
- This file and `project.faf` are a briefing, not policy — CONTRIBUTING / TESTING remain authoritative.
