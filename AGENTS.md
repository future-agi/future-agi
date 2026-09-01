# AGENTS.md — future-agi

Typed structured definitions live in [`project.faf`](project.faf) (`application/vnd.faf+yaml`), not in this prose. This file is the briefing; `project.faf` is the source of truth.

> Authored from `project.faf`. Re-author with `bunx faf-cli export --agents`, or hand-edit both. Nothing is added to dependencies or the build.

## What this project is

Open-source, end-to-end platform for evaluating, observing and improving LLM and AI-agent apps — one platform, one loop: simulate → evaluate → protect → monitor → optimize. Self-hostable (Apache-2.0) or managed Cloud. Main language: Python. Type: fullstack.

Human source of truth: [README.md](README.md), [CONTRIBUTING.md](CONTRIBUTING.md), [TESTING.md](TESTING.md). This file does not replace them.

## Where things live

| Path               | Role                                                                                                 |
| ------------------ | ---------------------------------------------------------------------------------------------------- |
| `futureagi/`       | Django backend (Python) — `tracer/`, `agentic_eval/`, `simulate/`, `accounts/`, `model_hub/`, `tfc/` |
| `frontend/`        | React + Vite (JavaScript)                                                                            |
| `agentcc-gateway/` | Go — OpenAI-compatible LLM gateway                                                                   |
| `fi-collector/`    | Go — OpenTelemetry collector                                                                         |
| `e2e/`             | Playwright full-stack flows                                                                          |

Framework instrumentors live in the separate `future-agi/traceAI` repo. Evaluator docs live in the docs repo.

## Setup

```bash
cp futureagi/.env.example futureagi/.env
docker compose up -d
yarn install                          # husky + lint-staged at repo root
cd futureagi && make pre-commit-install   # Python hooks
```

Backend: `http://localhost:8000`. Frontend: `http://localhost:3031`.

## Tests

```bash
cd futureagi && make test     # backend (pytest in Docker)
cd frontend && yarn test      # frontend (Vitest)
```

Before review: `make check-all` (backend) or `yarn check-all` (frontend). Full workflow, CI, and coverage: [TESTING.md](TESTING.md).

## Conventions

- **Python:** Black (line 88), isort (Black profile), mypy on new code. Obey `futureagi/pyproject.toml` and `futureagi/.pre-commit-config.yaml`.
- **JS / TS:** ESLint (Airbnb) + Prettier. Obey the frontend configs.
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`, `perf:`).
- **Branches:** `type/short-description` from `dev` (see [BRANCH_NAMING_CONVENTION.md](BRANCH_NAMING_CONVENTION.md)).
- **PRs:** branch from `dev`, keep the diff focused, add tests, sign the CLA on first PR ([CONTRIBUTING.md](CONTRIBUTING.md)).

Every bug fix needs a regression test. No hardcoded secrets, URLs, or PII.
