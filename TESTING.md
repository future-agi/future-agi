# Testing

Future AGI's testing pipeline runs in three layers, each catching a different class of problem before the next:

```
Developer machine     →     Git hooks     →     GitHub Actions
─────────────────           ─────────────       ───────────────
Run what you changed        Fast feedback       Full test suite
(yarn / make / bin/test)    (lint-staged)       (per-branch workflows)
```

Frontend, backend, and the Go collector have independent test runners. Run the suites affected by your change.

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
yarn test:api-journeys   # Browserless API journeys against a running backend
```

Coverage thresholds (global): **70%** for branches, functions, lines, and statements.

API journeys require `API_BASE` plus either `FUTURE_AGI_ACCESS_TOKEN` or
`FUTURE_AGI_EMAIL`/`FUTURE_AGI_PASSWORD`. Mutating annotation coverage is opt-in
with `API_JOURNEY_MUTATIONS=1`.

### Backend (Django + pytest, Compose dependencies)

```bash
cd futureagi

make test                # All tests
make test-unit           # Unit tests
make test-integration    # Integration tests
make test-watch          # Watch mode
make test-shell          # Interactive test shell
```

Under the hood, `make test` delegates to `bin/test`, which runs pytest with the local Python environment and starts dependencies in `docker-compose.test.yml` (Postgres, Redis, ClickHouse, and MinIO). See `bin/test --help` for lower-level options. The project name is fixed to `futureagi-test`; coordinate ownership before using it alongside another checkout.

### Collector and deployment contracts

Do **not** run the full collector suite on a shared host: it includes fixed-port
listeners and environment-gated integration writers. Run the Go test command
below only in a fresh, disposable **network-none network namespace** with its
own loopback, no host networking or published ports, and an explicit environment
allowlist excluding ambient `OBS_TEST_*` and `CH_TEST_*` variables. Do not enable
the `integration` build tag in this unit lane. Prepare dependencies separately;
do not give test execution external network access.

```bash
# Full suite: inside the isolated namespace described above, never on the host.
(cd fi-collector && go test -race -count=1 ./...)
(cd fi-collector && go build ./cmd/fi-collector ./cmd/fi-property-catalog-consumer ./cmd/fi-observed-catalog-backfill)
python3 -m unittest discover -s deploy/tests -p 'test_observed_catalog_*.py' -v
```

Report environment-gated integration skips separately from executed unit tests;
they do not qualify real ClickHouse/Kafka/PostgreSQL behavior. A non-race binary
run is not race qualification. Targeted in-memory/transport-only tests may run
directly under deny-all networking. Tests using `miniredis` or `httptest` need
only their own loopback listeners, never borrowed host services.

The deployment suite renders root/dev/E2E/production Compose, checks the startup dependency graph and mutation guards, and exercises index bootstrap with a fake ClickHouse client. It does not start containers or create databases. Passing configuration tests is not a fresh/retained startup proof. Runtime observation validation uses the existing E2E harness; see [`fi-collector/PROPERTY_CATALOG_OSS.md`](fi-collector/PROPERTY_CATALOG_OSS.md) for source-built images and the bounded backfill command.

### Observed catalog integration (isolated dependencies)

After confirming ports 18143, 19094 and 15543 are free and choosing a unique
project name, start only the three-service fixture from the repository root:

```bash
OBS_TEST_PROJECT="observed-catalog-local-$$"
docker compose --env-file /dev/null \
  -f fi-collector/test/observed-catalog/compose.yml \
  -p "$OBS_TEST_PROJECT" up -d --wait --wait-timeout 180

(cd fi-collector && \
  OBS_TEST_CH_URL=http://127.0.0.1:18143 \
  OBS_TEST_KAFKA_BROKER=127.0.0.1:19094 \
  OBS_TEST_PG_DSN=postgres://test:test@127.0.0.1:15543/observed_catalog_test \
  OBS_TEST_REPLICA_URLS='' GOMAXPROCS=2 \
  go test -race -count=1 -p 1 -timeout 8m -v \
    -run '^(TestClickHouse|TestKafka|TestPostgres|TestBackfillCLIThroughKafkaWithResume|TestObservedCatalogHTTPToLocalStack)' \
    ./cmd/fi-observed-catalog-backfill ./pkg/server)

# Only when finished with this disposable project you created:
docker compose --env-file /dev/null \
  -f fi-collector/test/observed-catalog/compose.yml \
  -p "$OBS_TEST_PROJECT" down --volumes --remove-orphans --timeout 10
```

For an already-running fixture owned by another task, run only the Go command
with its confirmed ports; do not start or tear down that project. Override
`OBS_TEST_CH_PORT`, `OBS_TEST_KAFKA_PORT`, `OBS_TEST_PG_PORT` and the matching
Go endpoints together if the defaults are occupied.

CI uses the same fixture, repeats OSS bootstrap, requires all ten integration
proofs to pass (missing/skipped tests fail), captures logs, and always cleans up
its unique run/attempt project. The HTTP test covers parsing, trusted scope,
async source write, spool restart, Kafka and index consumption; authentication
is a synthetic trusted result, not live PostgreSQL API-key authentication.
Three-replica qualification remains optional via `replicated.yml` and
`OBS_TEST_REPLICA_URLS`, outside the default CI job.

### End-to-end (browser + full stack, Playwright)

```bash
bin/e2e up      # boot the isolated futureagi-e2e stack (side by side with your dev stack)
bin/e2e test    # run the Playwright suite against it
bin/e2e ui      # Playwright UI mode
bin/e2e down    # stop it (add -v to wipe volumes)
```

Full-stack flows that drive a real browser against the production frontend image and assert both what the user sees **and** the backend state behind it (Postgres, ClickHouse, the CDC mirrors). The stack is the root `docker-compose.yml`, trimmed and moved onto its own ports (app 3100, API 8100, everything else 2xxxx), with separate default ports from dev and `futureagi-test`. The project name is fixed to `futureagi-e2e`; check ownership and ports before startup. The only mocked component is the LLM provider, behind the real gateway.

A cold first boot takes about 8–9 minutes — most of it the backend's first-run migrations — and warm boots are far quicker. Covered flows are cataloged in [`e2e/FLOWS.md`](e2e/FLOWS.md), which is generated from the specs (`cd e2e && yarn catalog`). The authoring guide, the attach and local-code workflows, and the quarantine rules are in [`e2e/README.md`](e2e/README.md). Two agent skills ship with the repo: `writing-e2e-flows` authors a flow from the feature's ticket and design doc, and `reviewing-prs` reviews a PR against the coding standards with an E2E-coverage gate (`cd e2e && yarn coverage`).

---

## Git hooks

Installed once per clone via `yarn install` at repo root (husky's `prepare` script wires `core.hooksPath=.husky/`).

### `pre-commit` (`.husky/pre-commit`)

Runs `lint-staged` against files currently staged. For frontend paths that means ESLint auto-fix + Prettier; Python formatting runs separately via `make pre-commit-install` inside `futureagi/`.

The hook auto-skips during `git merge` — merge-commit staging includes the entire merge diff, which makes linting every file pointless and slow.

### `pre-push` (`.husky/pre-push`)

Validates the branch name against the convention in `BRANCH_NAMING_CONVENTION.md`. Protected branches (`main`, `master`, `dev`, `develop`) skip validation. Applies to both frontend and backend commits.

### Bypassing hooks

Only in genuine emergencies, and document _why_ in the commit body:

```bash
git commit --no-verify -m "hotfix: critical bug"
git push --no-verify
```

If `--no-verify` becomes habitual, the hook is wrong — open an issue instead.

---

## CI (GitHub Actions)

CI covers frontend, sharded backend pytest, Go collector tests/builds, deployment contracts, and the end-to-end suite. Workflows in [`.github/workflows/`](.github/workflows):

| Workflow                           | Trigger                                                                          | Purpose                                                                                            |
| ---------------------------------- | -------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `frontend-feature.yml`             | push to `feat/*`, `fix/*`, `chore/*`, `docs/*`, `refactor/*`, `test/*`, `perf/*` | Branch-name validation, type check, unit tests, build verification                                 |
| `frontend-develop.yml`             | push to `develop`/`dev` + PRs into `main`/`develop`/`dev`                        | Quality gates, integration tests, build check, Lighthouse (PRs only)                               |
| `frontend-main.yml`                | push to `main`                                                                   | Full suite with coverage + production build                                                        |
| `frontend-deploy-*.yaml`           | manual or on main                                                                | Environment-specific deploys (EU, GCP, prod, dev CDN)                                              |
| `frontend-auto-approve-hotfix.yml` | hotfix PRs                                                                       | Auto-approval routing for verified hotfix branches                                                 |
| `backend-ci.yml`                  | Backend/deployment PR changes, pushes to `dev`/`main`, merge queue               | Sharded pytest using the standard test dependency stack                                          |
| `fi-collector-ci.yml`             | Collector/deployment PR changes, pushes to `dev`/`main`, merge queue, manual      | Go race tests/builds, real observation integration, Compose/bootstrap contracts and installer syntax       |
| `e2e-ci.yml`                       | PRs into and pushes on `dev`/`main`, merge queue                                 | Builds the changed images from PR code, boots the `futureagi-e2e` stack, runs the Playwright flows |

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

pytest discovers them automatically. Use `bin/test path/to/tests/test_foo.py::test_bar` when you need granular selection.

---

## Troubleshooting

**Hooks don't fire after cloning.** Run `yarn install` at repo root — husky's `prepare` script sets `core.hooksPath` during install, and it's a no-op until that runs.

**Pre-commit fails on files you didn't touch.** `lint-staged` only runs against _your_ staged files, so this usually means you accidentally staged more than intended. Check `git diff --cached --stat`.

**Tests pass locally but fail in CI.** Run `yarn test:ci` (frontend) or `make test` (backend) — both use the same reporter/environment as CI.

**Backend test dependencies are stuck.** Inspect `bin/test status` and `bin/test logs` first. `bin/test down` removes the `futureagi-test` volumes; use it only for a disposable test stack you own, never to free resources used by another checkout.

**TypeScript errors block a commit.** Frontend doesn't currently ship a `tsconfig.json`, so `yarn type-check` is a no-op. If someone adds TypeScript, that gate starts working automatically.

---

## What we're still missing

- Broader E2E flow coverage — the harness and CI job ship today; [`e2e/FLOWS.md`](e2e/FLOWS.md) is the current list
- Visual regression testing (Chromatic or Percy)
- Accessibility testing (axe-core in CI)
- Performance budgets with automated alerts

Contributions welcome — open an issue before starting work on any of these so we can align on scope.
