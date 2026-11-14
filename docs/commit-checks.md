# Commit Checks

The repo uses Husky and lint-staged for fast commit-level checks. The hook only
checks staged files, which keeps normal commits quick while still catching the
highest-signal failures before CI.

## What Runs On Commit

- Staged safety checks for merge conflict markers, focused tests, debug
  statements, and accidental personal files such as `.DS_Store`, `.env`, local
  MCP config, or personal Claude settings.
- Backend Python files under `futureagi/`: `ruff check --fix`, then
  `ruff format`.
- Frontend files under `frontend/`: `eslint --fix` for JS/TS, then `prettier`.
- Repo-level JSON/YAML/Markdown and docs: `prettier`.
- `api_contracts/filter_contract.json`: prettier plus frontend contract checks.

Generated frontend contract files and generated OpenAPI JSON are intentionally
excluded from formatting hooks. They should be updated through the contract
generation commands.

## Install

```bash
./scripts/setup-commit-checks.sh
```

The script installs missing root/frontend/backend dev dependencies, installs the
Husky hooks, and verifies that `lint-staged` can load the repo config. If your
dependencies are already installed and you only want to verify hooks, run:

```bash
SKIP_INSTALL=1 ./scripts/setup-commit-checks.sh
```

## Run Manually

```bash
yarn check:staged-safety
yarn lint-staged
yarn contracts:check
cd futureagi && uv run ruff check .
cd futureagi && uv run ruff format --check .
```

## Operating Model

The commit hook should stay fast and staged-file based. It is there to catch
high-signal mistakes before code leaves a laptop, not to replace CI.

Use pre-commit for:

- staged formatting and lint auto-fixes
- obvious safety failures
- contract checks only when the contract source file changed

Use CI for:

- full backend pytest
- full frontend Vitest and Playwright
- full OpenAPI generation and drift checks
- Django migration/system checks
- security and dependency scans

This mirrors the shape used by mature OSS repos such as PostHog: local hooks run
`lint-staged` quickly, while generated schemas, test matrices, and expensive
checks remain CI-owned.

## What Belongs In CI Instead

Keep slow or environment-heavy checks in CI or pre-push, not pre-commit:

- Full backend pytest matrix.
- Full frontend Vitest and browser tests.
- Full OpenAPI generation and drift check.
- Django migrations/system checks.
- Security scans and dependency audits.
