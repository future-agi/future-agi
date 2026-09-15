<!-- faf:start -->
<!-- faf: future-agi | Python | fullstack | Open-source, end-to-end platform for evaluating, observing and improving LLM and AI-agent applications — tracing, evals, simulations, guardrails, an OpenAI-compatible gateway and optimization, on one platform and one feedback loop from first prototype to live deployment. Self-hostable (Apache-2.0) or managed Future AGI Cloud. -->
<!-- faf: claim=project.faf | family=FAF -->

# AGENTS.md — future-agi

Open-source, end-to-end platform for evaluating, observing and improving LLM and AI-agent applications — tracing, evals, simulations, guardrails, an OpenAI-compatible gateway and optimization, on one platform and one feedback loop from first prototype to live deployment. Self-hostable (Apache-2.0) or managed Future AGI Cloud. — Python · type: fullstack

> Authored by faf — do not edit the managed block; refresh with `faf export --agents`. Hand content outside `<!-- faf:start -->` … `<!-- faf:end -->` is preserved.

## Setup & build

```bash
yarn install    # install
cp futureagi/.env.example futureagi/.env && docker compose up -d    # boot
cd futureagi && make pre-commit-install    # hooks
```

## Run the tests

```bash
cd futureagi && make test
cd frontend && yarn test
cd futureagi && make check-all
cd frontend && yarn check-all
```

## Where things live

| Path | Role |
|------|------|
| `futureagi/` | Django 5.1 + DRF backend — tracer, agentic_eval, simulate, accounts, model_hub, mcp_server, tfc |
| `frontend/` | React 18 + Vite 5 (JavaScript, ESM) — MUI v5, TanStack Query, Zustand |
| `agentcc-gateway/` | Go — OpenAI-compatible LLM gateway |
| `fi-collector/` | Go — OTLP→ClickHouse span collector |
| `e2e/` | Playwright full-stack flows (`bin/e2e`) |
| `.agents/skills/` | Repo agent skills — writing-e2e-flows, reviewing-prs |
| `CONTRIBUTING.md` | Setup, code style, PR checklist, project layout |
| `TESTING.md` | Test runners, git hooks, CI matrix, coverage thresholds |

## Conventions

- Python — Ruff + Black (line length 88), isort (Black profile), mypy on new code; CI rejects new type errors.
- JavaScript — ESLint (Airbnb) + Prettier. No tsconfig.json yet, so `yarn type-check` is a no-op.
- Commits — Conventional Commits (`feat` / `fix` / `chore` / `docs` / `refactor` / `test` / `perf`).
- Branches — `type/short-description` off `dev`, validated on `git push` (see BRANCH_NAMING_CONVENTION.md).
- PRs — base from `dev`, keep the diff focused, add a regression test for every bug fix, sign the CLA on your first PR.
- Evaluators — live under `futureagi/agentic_eval/core_evals/fi_evals/`: a class, a rubric prompt (if LLM-judge), a registration in `eval_type.py`, and tests.
- Backend and frontend have independent test runners — you rarely need to run both.

## Guardrails

- Base branch is `dev`, not `main` — branch from `dev` and open PRs into `dev`.
- Polyglot repo — Django in `futureagi/`, React in `frontend/`, Go in `agentcc-gateway/` + `fi-collector/`. Don't edit the frontend for a backend bug.
- Framework instrumentors are NOT in this repo — they live in `future-agi/traceAI`. Evaluator reference docs live in the docs repo.
- This file and `project.faf` are a briefing, not policy — README / CONTRIBUTING / TESTING stay authoritative.
- **Always OK:** read the tree · run the tests (`cd futureagi && make test`) · `cd futureagi && make check-all`.
- **Ask first:** dependency installs, deletions, migrations, schema changes, publish/release.
- **Never:** force-push · push straight to `dev` (branch and open a PR) · commit secrets.

## Definition of Done

Done when: `cd futureagi && make check-all` exits 0 · `cd frontend && yarn check-all` exits 0 · `cd futureagi && make test` passes · `cd frontend && yarn test` passes · changes committed with a conventional message.

## When stuck

Ask a clarifying question, propose a short plan, or open a draft PR with notes — do not push large speculative changes to `dev`.

## Security & secrets

- Secrets live in `futureagi/.env` (see `futureagi/.env.example`). Never read or commit them.
- Never read or commit `futureagi/.env`.

## Commit & PR

- Conventional Commits preferred (`feat:`, `fix:`, `chore:`, …).
- Branch off `dev` and open a PR — never commit to `dev` directly.
- If build/test scripts or layout change, refresh this file in the **same PR** (`faf export --agents`).

## Stack

- **Framework:** React 18 + Vite 5 (JavaScript, ESM) — frontend/
- **CSS:** Emotion (CSS-in-JS, via MUI)
- **UI Library:** MUI v5 (@mui/material, @mui/lab, @mui/x-data-grid, @mui/x-date-pickers), AG Grid
- **State:** TanStack Query v5 (server state) + Zustand v5 (client state); react-hook-form
- **Backend:** Django 5.1 + Django REST Framework (Python >=3.11) — futureagi/; Granian ASGI server; Celery + Temporal workers
- **API:** REST (Django REST Framework); OpenAI-compatible HTTP at the gateway; OTLP ingest for traces
- **Runtime:** Docker / Docker Compose (backend :8000, frontend :3031)
- **Database:** PostgreSQL (primary) · ClickHouse (spans / traces) · Redis (cache + broker) · Temporal (workflows) · Kafka (property-catalog pipeline)
- **Connection:** Django ORM + psycopg; clickhouse-connect / clickhouse-driver for ClickHouse
- **Hosting:** Self-hostable via Docker Compose, or managed Future AGI Cloud
- **Build:** Vite (frontend) · Make + Docker (backend) · Go toolchain (agentcc-gateway, fi-collector) · release-please for versioning
- **CI/CD:** GitHub Actions — per-branch frontend workflows (feature / develop / main) + e2e-ci.yml Playwright on dev/main
- **Package Manager:** yarn (repo root + frontend/) · uv / pip (futureagi/, pyproject.toml) · Go modules (agentcc-gateway/, fi-collector/)
- **Admin:** Django admin
- **Cache:** Redis
- **Search:** ChromaDB / Qdrant / Weaviate client libraries (vector stores for eval + RAG)
- **Storage:** MinIO (S3-compatible object storage)

*Context authored: 2026-09-01*
<!-- faf:end -->
