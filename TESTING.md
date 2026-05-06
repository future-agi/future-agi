# Testing

Future AGI's testing pipeline runs in three layers, each catching a different class of problem before the next:

```
Developer machine     →     Git hooks     →     GitHub Actions
─────────────────           ─────────────       ───────────────
Run what you changed        Fast feedback       Full test suite
(yarn / make / bin/test)    (lint-staged)       (per-branch workflows)
```

Frontend and backend have independent test runners. You rarely need to run both.

- Frontend-specific conventions: [`frontend/TESTING.md`](frontend/TESTING.md)
- Backend runner internals: `futureagi/bin/test --help`
- Branch naming (enforced on push): [`BRANCH_NAMING_CONVENTION.md`](BRANCH_NAMING_CONVENTION.md)

---

## Running tests locally

### Frontend (React + Vite, Vitest + React Testing Library)

```bash
cd frontend

yarn test                # Interactive watch mode
yarn test:run            # One-shot run
yarn test:changed        # Only tests related to changed files
yarn test:coverage       # Full run with coverage report
yarn test:unit           # Unit tests only
yarn test:integration    # Integration tests only
```

Coverage thresholds (global): **70%** for branches, functions, lines, and statements.

### Backend (Django + pytest, Docker-based)

```bash
cd futureagi

make test                # All tests
make test-unit           # Unit tests
make test-integration    # Integration tests
make test-watch          # Watch mode
make test-shell          # Shell inside the test container
```

Under the hood, `make test` delegates to `bin/test`, which runs pytest inside an isolated Compose stack (`docker-compose.test.yml`) with its own Postgres, Redis, ClickHouse, and MinIO. See `bin/test --help` for lower-level options.

---

## Git hooks

Installed once per clone via `yarn install` at repo root (husky's `prepare` script wires `core.hooksPath=.husky/`).

### `pre-commit` (`.husky/pre-commit`)

Runs `lint-staged` against files currently staged. For frontend paths that means ESLint auto-fix + Prettier; Python formatting runs separately via `make pre-commit-install` inside `futureagi/`.

The hook auto-skips during `git merge` — merge-commit staging includes the entire merge diff, which makes linting every file pointless and slow.

### `pre-push` (`.husky/pre-push`)

Validates the branch name against the convention in `BRANCH_NAMING_CONVENTION.md`. Protected branches (`main`, `master`, `dev`, `develop`) skip validation. Applies to both frontend and backend commits.

### Bypassing hooks

Only in genuine emergencies, and document *why* in the commit body:

```bash
git commit --no-verify -m "hotfix: critical bug"
git push --no-verify
```

If `--no-verify` becomes habitual, the hook is wrong — open an issue instead.

---

## CI (GitHub Actions)

Today, CI covers the frontend; backend CI is on the roadmap. Workflows in [`.github/workflows/`](.github/workflows):

| Workflow | Trigger | Purpose |
|---|---|---|
| `frontend-feature.yml` | push to `feat/*`, `fix/*`, `chore/*`, `docs/*`, `refactor/*`, `test/*`, `perf/*` | Branch-name validation, type check, unit tests, build verification |
| `frontend-develop.yml` | push to `develop`/`dev` + PRs into `main`/`develop`/`dev` | Quality gates, integration tests, build check, Lighthouse (PRs only) |
| `frontend-main.yml` | push to `main` | Full suite with coverage + production build |
| `frontend-deploy-*.yaml` | manual or on main | Environment-specific deploys (EU, GCP, prod, dev CDN) |
| `frontend-auto-approve-hotfix.yml` | hotfix PRs | Auto-approval routing for verified hotfix branches |

A push to a feature branch runs only `frontend-feature.yml`, not the main or develop pipelines. This keeps GitHub Actions minutes targeted — no overlapping workflows.

---

## Test layout

### Frontend

Tests live next to the components they test, or under per-feature `__tests__/` folders:

```
frontend/src/
├── components/Button/
│   ├── Button.jsx
│   └── Button.test.jsx        ← component-local tests
└── __tests__/
    ├── unit/                  ← fast, isolated (ideal for pre-commit)
    ├── integration/           ← component + deps (CI)
    └── e2e/                   ← end-to-end (CI only, not yet running)
```

Custom render helper: `frontend/src/utils/test-utils.jsx` wraps providers (Theme, Settings, Router) — use it instead of `@testing-library/react`'s bare `render()`.

### Backend

Tests live inside each Django app:

```
futureagi/
├── tracer/tests/
├── agentic_eval/tests/
├── simulate/tests/
├── mcp_server/tests/
└── accounts/tests/
```

pytest discovers them automatically. Use `make test-shell` to drop into the test container and run `pytest path/to/tests/test_foo.py::test_bar` when you need granular selection.

---

## Troubleshooting

**Hooks don't fire after cloning.** Run `yarn install` at repo root — husky's `prepare` script sets `core.hooksPath` during install, and it's a no-op until that runs.

**Pre-commit fails on files you didn't touch.** `lint-staged` only runs against *your* staged files, so this usually means you accidentally staged more than intended. Check `git diff --cached --stat`.

**Tests pass locally but fail in CI.** Run `yarn test:ci` (frontend) or `make test` (backend) — both use the same reporter/environment as CI.

**Backend test container is stuck.** `bin/test down` tears it down; `bin/test up` brings it back. The Compose project is `futureagi-test` so it won't collide with `docker-compose.yml`.

**TypeScript errors block a commit.** Frontend doesn't currently ship a `tsconfig.json`, so `yarn type-check` is a no-op. If someone adds TypeScript, that gate starts working automatically.

---

## What we're still missing

- Backend CI (pytest in Actions, parallel to the frontend workflows)
- Playwright E2E for critical user journeys
- Visual regression testing (Chromatic or Percy)
- Accessibility testing (axe-core in CI)
- Performance budgets with automated alerts

Contributions welcome — open an issue before starting work on any of these so we can align on scope.
