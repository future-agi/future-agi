# Contributing to Future AGI

Thanks for your interest in contributing! This project exists because of people like you.

Future AGI is an open-source AI evaluation and observability platform, and we welcome contributions of all kinds: bug fixes, new evaluators, framework integrations, docs improvements, examples, and issue triage.

---

## Quick links

- 🐛 [Report a bug](https://github.com/future-agi/future-agi/issues/new?template=bug_report.yml)
- ✨ [Request a feature](https://github.com/future-agi/future-agi/issues/new?template=feature_request.yml)
- 🔖 [Good first issues](https://github.com/future-agi/future-agi/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
- 💬 [Join Discord](https://discord.gg/futureagi)
- 📖 [Architecture overview](https://docs.futureagi.com/architecture)

---

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). By participating, you agree to uphold it. Report unacceptable behavior to **conduct@futureagi.com**.

---

## Contributor License Agreement (CLA)

Before we can merge your first pull request, you'll need to sign our Contributor License Agreement. This is a one-click process that runs automatically on your first PR — you'll see a link to sign, we merge after.

The CLA grants Future AGI, Inc. the rights to use your contribution (including an Apache-style patent grant), while letting you retain copyright. It also lets us re-license portions of the project later if needed (e.g. for a future `/ee/` folder). The full text will be linked in the bot prompt when you open your first PR.

---

## Development setup

### 1. Fork and clone

```bash
gh repo fork future-agi/future-agi --clone
cd future-agi
```

### 2. Start the stack

```bash
cp .env.example .env
docker compose up -d
```

The backend will be at `http://localhost:8000`, the frontend at `http://localhost:3031`.

### 3. Install git hooks

```bash
# From repo root — installs Husky hooks and lint-staged tooling.
yarn install

# For Python hooks (ruff lint + format):
cd futureagi && make pre-commit-install
```

On every commit, staged files are checked automatically:

- `frontend/src/**` → ESLint + Prettier (via lint-staged)
- `futureagi/**/*.py` → ruff lint + ruff format (via pre-commit)

Branch names are validated on `git push`.

### 4. Run tests

```bash
# Backend
cd futureagi && make test

# Frontend
cd frontend && yarn test
```

Full testing workflow — git hooks, CI pipeline, coverage thresholds, frontend/backend-specific commands — lives in [TESTING.md](TESTING.md). Backend setup: [futureagi/README.md](futureagi/README.md). Frontend conventions and commands: [frontend/TESTING.md](frontend/TESTING.md).

---

## How to contribute

### 🐛 Reporting bugs

Before filing, search [existing issues](https://github.com/future-agi/future-agi/issues) to see if it's already reported. A good bug report includes:

- Future AGI version (`git rev-parse HEAD` if self-hosted; see the settings page in Cloud)
- Environment: OS, Python / Node version, Docker version
- Exact reproduction steps
- Expected vs. actual behavior
- Relevant logs or stack traces

### ✨ Proposing features

For anything larger than a few hours of work, **open an issue first** so we can discuss design before you write code. This saves everyone time.

Good feature requests:
- Describe the problem you're trying to solve
- Show (don't tell) — a mockup, a code snippet of the desired API
- List alternatives you considered
- Call out anything you're unsure about

### 🔧 Your first PR

1. Pick a [`good first issue`](https://github.com/future-agi/future-agi/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) (or open one)
2. Comment that you're working on it so we don't double-up
3. Branch from `main`: `git checkout -b fix/short-description`
4. Make your change — keep the diff small and focused
5. Add tests (every bug fix needs a regression test)
6. Make sure `make check-all` passes
7. Push and open a PR using the template
8. Sign the CLA when the bot asks

### 🧪 Adding a new evaluator

Most evaluators live under `futureagi/agentic_eval/core_evals/fi_evals/`. Each evaluator needs:

1. A Python class extending `BaseEvaluator` or `LLMEvaluator`
2. A rubric prompt (if LLM-as-judge) — in the evaluator's own `prompt.py`
3. A registration entry in `eval_type.py`
4. Tests in the nearest `tests/` directory
5. Docs in `docs/evaluators/` *(separate docs repo)*

See [adding an evaluator](https://docs.futureagi.com/evaluators/custom) for the full walkthrough.

### 🧩 Adding a framework integration

Framework integrations live in the `traceAI` SDK (separate repo). For Python: [github.com/future-agi/traceAI](https://github.com/future-agi/traceAI). Add an instrumentor subclassing `BaseInstrumentor` in `traceloop-sdk/` — see the LangChain and LlamaIndex instrumentors for the pattern.

---

## Code style

We follow:

- **Python:** PEP 8 via ruff (line length 88); `ruff format` replaces Black, `ruff check --fix` handles imports
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
- [ ] `make check-all` (backend) or `yarn check-all` (frontend) passes
- [ ] Docstrings on new public APIs
- [ ] Release notes updated if user-facing (add an entry to the GitHub release)
- [ ] No hardcoded secrets, URLs, or PII
- [ ] CLA signed (bot will prompt on first PR)

Your PR will be reviewed by a maintainer within **3 business days**. If it's been longer, feel free to `@mention` one of us.

---

## Project layout

```
future-agi/
├── futureagi/              # Django backend (Python)
│   ├── tracer/             # OpenTelemetry ingest + trace APIs
│   ├── agentic_eval/       # Evaluation framework
│   ├── simulate/           # Agent simulation
│   ├── accounts/           # Auth, orgs, workspaces
│   ├── model_hub/          # LLM / embedding hub
│   ├── agentcc-gateway/    # Prism LLM gateway (Go)
│   ├── tfc/                # Django project settings + routing
│   └── ...
└── frontend/               # React + Vite (JavaScript)
```

---

## Releases

We release a new version of the Docker images roughly every two weeks, and SDK minor versions as features land. We follow [SemVer](https://semver.org/).

Release notes live in [GitHub Releases](https://github.com/future-agi/future-agi/releases).

---

## Thanks

We read every issue and PR. If we don't respond within a few days, it's not intentional — please ping us. We're a small team trying to build something great with you.

Star the repo ⭐, join Discord 💬, and ship something you're proud of. ❤️
