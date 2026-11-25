# Contributing to Future AGI

Thanks for your interest in contributing! This project exists because of people like you.

Future AGI is an open-source AI evaluation and observability platform, and we welcome contributions of all kinds: bug fixes, new evaluators, framework integrations, docs improvements, examples, and issue triage.

**Read [CONTRIBUTION_POLICY.md](CONTRIBUTION_POLICY.md) first.** It is one page: the four rules we hold every outside PR to, what gets a PR closed without full review, and what gets one merged fast. This file is the practical how-to; the policy is the contract.

---

## Quick links

- 🐛 [Report a bug](https://github.com/future-agi/future-agi/issues/new?template=bug_report.yml)
- ✨ [Request a feature](https://github.com/future-agi/future-agi/issues/new?template=feature_request.yml)
- 🔖 [Good first issues](https://github.com/future-agi/future-agi/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
- 💬 [Join Discord](https://discord.com/invite/n2tCUKBkAw)
- [Automatic-update developer guide](deploy/update/DEVELOPER_GUIDE.md) — requirements for migrations, configuration, startup, storage and recovery changes

---

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). By participating, you agree to uphold it. Report unacceptable behavior to **conduct@futureagi.com**.

---

## Contributor License Agreement (CLA)

Before we can merge your first pull request, you'll need to sign our Contributor License Agreement. This is a one-click process that runs automatically on your first PR — you'll see a link to sign, we merge after.

The CLA grants Future AGI, Inc. the rights to use your contribution (including an Apache-style patent grant), while letting you retain copyright. It also lets us re-license portions of the project later if needed (e.g. for a future `/ee/` folder).

---

## Development setup

### 1. Fork and clone

```bash
gh repo fork future-agi/future-agi --clone
cd future-agi
```

### 2. Start the stack with hot reload

```bash
./bin/dev
```

The first run builds every image from your checkout and installs through
`./bin/install --from-source` (it writes `.env` and asks for your first
account); give Docker 8 GB of memory for that build. After that, the UI is at
<http://localhost:3000> (Vite, hot module replacement) and the API at
<http://localhost:8000>, which restarts when you save a Python file under
`futureagi/`.

Everyday commands: `./bin/dev logs`, `./bin/dev shell`,
`./bin/dev manage migrate`, `./bin/dev rebuild` after a dependency change,
`./bin/dev down`. [Local development](https://docs.futureagi.com/docs/self-hosting/development) covers what
reloads, migrations, tests, when to use `./bin/dev --distributed`, and
troubleshooting.

### 3. Install git hooks

```bash
# From the repository root:
yarn install
```

The root install sets Git’s `core.hooksPath` to `.husky/_`. On a non-merge commit,
Husky runs the [lint-staged tasks](.lintstagedrc.cjs), including staged safety checks.
It does not install or run `futureagi/.pre-commit-config.yaml`.

- **Backend Python:** For staged `futureagi/**/*.py` files outside migrations, the [Python runner](scripts/lint-staged-python.sh) runs `ruff check --fix`, then `ruff format`. Install `uv` or `uvx` to run the CI-pinned Ruff. Offline, the runner can use `ruff` on `PATH` and warns that CI may differ. If no runner is available, the commit stops.
- **Formatting:** Run `yarn --cwd frontend install` before staging files that need ESLint or Prettier. This includes frontend source and files formatted with the frontend copy of Prettier, such as Markdown, the root `package.json`, and GitHub workflow YAML.

On `git push`, Husky checks the destination name of each branch you create or update, even when you push from a detached `HEAD`.

### 4. Run tests

```bash
# Backend: a host virtualenv, with Postgres, Redis, ClickHouse and MinIO in their own test containers
cd futureagi && uv sync --frozen && make test

# Frontend
cd frontend && yarn test
```

Tests never touch the `./bin/dev` stack. Full testing workflow — git hooks, CI pipeline, coverage thresholds, frontend/backend-specific commands — lives in [TESTING.md](TESTING.md). Backend setup: [futureagi/README.md](futureagi/README.md). Frontend conventions and commands: [frontend/README.md](frontend/README.md).

---

## How to contribute

### 🐛 Reporting bugs

Before filing, search [existing issues](https://github.com/future-agi/future-agi/issues) to see if it's already reported. A good bug report includes:

- Future AGI version (self-hosted: `FUTURE_AGI_VERSION` in `.env`, or `git rev-parse HEAD` for a source build; see the settings page in Cloud)
- Setup: Standalone, Distributed or Helm, and whether you use `./bin/dev`
- Environment: OS, Python / Node version, Docker version
- Exact reproduction steps
- Expected vs. actual behavior
- Relevant logs or stack traces

### ✨ Proposing features

For anything larger than a few hours of work, **open an issue first** and wait for a maintainer to label it `accepted` before you write code. PRs for un-accepted work are closed with a pointer back to the issue (see the [policy](CONTRIBUTION_POLICY.md)). This saves everyone time, yours most of all.

Good feature requests:

- Describe the problem you're trying to solve
- Show (don't tell) — a mockup, a code snippet of the desired API
- List alternatives you considered
- Call out anything you're unsure about

### 🔧 Your first PR

1. Pick a [`good first issue`](https://github.com/future-agi/future-agi/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) or an issue labelled `accepted` (or open one and wait for the label)
2. Comment that you're working on it so we don't double-up
3. Branch from `dev`: `git checkout -b fix/short-description`
4. Make your change — keep the diff small and focused
5. Add tests (every bug fix needs a regression test that fails without the fix)
6. Run it for real: `make check-all`, and the affected app's tests via `bin/test app <name>`
7. Push and open a PR using the template. Paste the actual test output; fill the `E2E:` and `AI use:` lines
8. Sign the CLA when the bot asks

If you are an automated agent opening this PR on someone's behalf, add `[agent]` to the end of the title so we can route it to the streamlined agent-review lane.

Outside contributors can have **three PRs open at a time**. After two merges you're added to the bypass list and the limit no longer applies.

### Cloud worker preflight

Before the first push and after each repair, run the mechanical checks on an authorized, isolated cloud worker. Use a clean, committed candidate checkout with full history, Bash, Python 3.11 and an approved package source. The worker needs no queue credentials.

The controller supplies `BASE_OID`, the immutable tip of the intended upstream base, and runs the following from the candidate's root **as controller-owned commands**, not by extracting commands from this document. Load the [preflight script](.github/merge-queue/cloud-preflight.sh), both gates, the Ruff settings and the Ruff version from that same protected commit. If the base lacks the preflight, hold the candidate; never fall back to its copy.

```bash
set -euo pipefail
export BASE_OID="${BASE_OID:?need the upstream base commit}"
[[ "$BASE_OID" =~ ^[0-9a-f]{40}$ ]]
preflight="$(mktemp)"
trap 'rm -f -- "$preflight"' EXIT
git show "${BASE_OID}:.github/merge-queue/cloud-preflight.sh" > "$preflight"
[[ -s "$preflight" ]]
bash "$preflight"
```

Run the affected tests with their normal CI prerequisites as well; a clean preflight is not a required-check verdict. If the base, diff or affected scope is uncertain, resolve it or run the full relevant suites. Never treat missing evidence as a pass.

### Factory retries and queue admission

- **One active revision per ticket.** Keep the failing SHA, job, logs and artifacts. Fix a code or assertion failure once, then stop and return the second real failure for diagnosis, whether it occurred in preflight or CI. Do not rerun an assertion or open a replacement PR to reset the limit. At most one failed-job retry per still-current SHA is allowed for an identified transient infrastructure failure; record its cause and retain both attempts.
- **Admission is outside CI.** The trusted controller throttles both launches and queue entries to available, authorized capacity. It reads the [factory policy](.github/merge-queue/admission-policy.json) from the protected base, never the PR. Neither this file, a green preflight, nor the `admitted` label from `admission.yml` grants permission to spend, merge or waive a check. Public workflows do not receive a controller write token.
- **Enqueue once.** Serialize by repository and PR, and deduplicate by PR and head OID. Re-read the live PR before enqueue: it must be open, non-draft, target `dev`, and still have the explicitly authorized head, required approvals and successful required checks. Hold on any missing or stale signal. If already queued for that head, do nothing; otherwise use `enqueuePullRequest` with `expectedHeadOid` set to that exact OID and `jump: false`. Reconcile an uncertain API result with the live queue before resubmitting. A removed entry needs a new decision, not an automatic re-enqueue. The merge group still needs its own required checks.
- **Keep failures visible.** Quarantine only specific test IDs under the existing [backend](futureagi/.test_quarantine.json) and [E2E](e2e/.quarantine.json) policies, with an owner, issue, reason and expiry. Independent full controls must execute those IDs and report their outcomes. Track first-attempt green rate, actual runner time spent before cancellation, remaining time avoided against comparable completed runs, and off-Actions preflight cost separately.

### 🧪 Adding a new evaluator

Most evaluators live under `futureagi/agentic_eval/core_evals/fi_evals/`. Each evaluator needs:

1. A Python class extending `BaseEvaluator` or `LLMEvaluator`
2. A rubric prompt (if LLM-as-judge) — in the evaluator's own `prompt.py`
3. A registration entry in `eval_type.py`
4. Tests in the nearest `tests/` directory
5. Docs in `docs/evaluators/` _(separate docs repo)_

See [adding an evaluator](https://docs.futureagi.com/docs/evaluation/features/custom) for the full walkthrough.

### 🧩 Adding a framework integration

Framework integrations live in the `traceAI` SDK (separate repo). For Python: [github.com/future-agi/traceAI](https://github.com/future-agi/traceAI). Add an instrumentor subclassing `BaseInstrumentor` in `traceloop-sdk/` — see the LangChain and LlamaIndex instrumentors for the pattern.

---

## Code style

We follow:

- **Python:** PEP 8 via Ruff: `ruff check` and `ruff format` (line length 88)
- **Imports:** sorted by Ruff's isort rules (`I`)
- **Types:** new code must pass `mypy` (we use a baseline for existing code)
- **JS / TS:** ESLint (Airbnb) + Prettier
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`, `perf:`)
- **Branch names:** `type/short-description` (e.g. `fix/session-list-pagination`)

Run `make format` to auto-fix most issues.

---

## Pull-request checklist

Before requesting review:

- [ ] PR description explains **what** and **why** (not just **how** — the diff shows that)
- [ ] Tests added or updated
- [ ] If changing migrations, configuration, startup or persistent storage, follow the [automatic-update developer checklist](deploy/update/DEVELOPER_GUIDE.md)
- [ ] `make check-all` (backend) or `yarn check-all` (frontend) passes
- [ ] Docstrings on new public APIs
- [ ] [CHANGELOG](https://futureagi.com/changelog) updated if user-facing
- [ ] User docs changed in [future-agi/docs](https://github.com/future-agi/docs) (docs.futureagi.com), not under `docs/`. A new environment variable is a row in `deploy/env-reference.toml`, which the [configuration reference](https://docs.futureagi.com/docs/self-hosting/configuration/reference) is generated from; telemetry fields and image facts likewise live in `wire_reference.toml` and `deploy/images.toml` ([TESTING.md](TESTING.md#images-docs-and-the-helm-chart))
- [ ] No hardcoded secrets, URLs, or PII
- [ ] `AI use:` line filled in (tool and extent, or `none`)
- [ ] Linked issue is labelled `accepted` (or the change is a small fix)
- [ ] CLA signed (bot will prompt on first PR)

Your PR will be reviewed by a maintainer within **3 business days**. If it's been longer, feel free to `@mention` one of us.

---

## Project layout

```
future-agi/
├── futureagi/        # Django backend (Python)
│   ├── tracer/       # OpenTelemetry ingest + trace APIs
│   ├── agentic_eval/ # Evaluation framework
│   ├── simulate/     # Agent simulation
│   ├── accounts/     # Auth, orgs, workspaces
│   ├── model_hub/    # LLM / embedding hub
│   ├── tfc/          # Django project settings + routing
│   └── ...
├── frontend/         # React + Vite (JavaScript)
├── fi-collector/     # OTLP trace collector (Go)
├── agentcc-gateway/  # LLM gateway (Go)
├── deploy/           # Standalone app image (standalone/), production overlay, Helm chart (helm/futureagi/)
├── bin/              # install, uninstall, dev, e2e
├── docs/             # maintainer notes; user docs live in future-agi/docs
└── e2e/              # Playwright end-to-end flows
```

---

## Releases

We release a new version of the Docker images roughly every two weeks, and SDK minor versions as features land. We follow [SemVer](https://semver.org/).

Release notes live at [futureagi.com/changelog](https://futureagi.com/changelog).

---

## Thanks

We read every issue and PR. If we don't respond within a few days, it's not intentional — please ping us. We're a small team trying to build something great with you.

Star the repo ⭐, join Discord 💬, and ship something you're proud of. ❤️
