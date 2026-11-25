# Core Backend

## Setup

Run the commands below from the `futureagi/` directory of the repository.

### Initial Setup
```bash
git submodule update --init --recursive
```

### Quick Start

With `uv` and Python 3.11 installed, run these commands from `futureagi/`:

```bash
uv sync --frozen --extra dev
. .venv/bin/activate
pre-commit validate-config .pre-commit-config.yaml
```

The `dev` extra supplies `pre-commit`; `requirements.txt` alone does not.
Validation checks the configuration without installing a Git hook.

### Type Checking with MyPy

CI runs mypy on the whole backend in the "Python types (advisory)" workflow.
Known errors are listed in `mypy-baseline.txt`, one line per error, with line
numbers set to 0 so moving code does not change them. The check fails when a
file has more errors of a kind than the baseline lists, including errors a
change causes in files it did not touch. It is advisory for now.

```bash
make mypy                # whole-repo mypy, compared with mypy-baseline.txt
make mypy-baseline-sync  # rewrite mypy-baseline.txt from a whole-repo run
```

mypy's Django plugin loads the production settings, `tfc.settings.settings`
(`[tool.django-stubs]` in `pyproject.toml`), with
`EE_LICENSE_KEY=mypy-placeholder` so the EE apps are registered too. The
variable is a placeholder, not a key; `make mypy` and CI set it, and both run
`.venv/bin/mypy`.

Fix a new error rather than syncing it into the baseline. After fixing
baselined errors, run `make mypy-baseline-sync` and commit the smaller
baseline. CI's environment is the reference (no `ee/cloud` checkout); its run
uploads mypy's output as the `mypy-output` artifact, which
`python3 -I ../scripts/mypy_baseline.py sync --input <file>` turns into a
baseline. See the [baseline helper](../scripts/mypy_baseline.py) and the
[type-checking conventions](../CONTRIBUTING.md#code-style).

### Pre-commit Hooks

#### First-time setup

Choose one hook setup per clone. To check for a custom hook directory, run
`git config --get core.hooksPath` from `futureagi/`. No output and exit status 1
means it is unset.

- **Repository hooks (Husky):** `yarn install` at the repository root configures
  Git to use Husky. Keep that hook path; do not run `make pre-commit-install`
  in this clone. See the [repository hook reference](../TESTING.md#git-hooks).
- **Standalone Python hook:** In a clone with no `core.hooksPath` set, complete
  the [Quick Start](#quick-start), then run `make pre-commit-install` from
  `futureagi/`. This installs the backend checks in Git's default hook directory.

#### Daily usage

With the Quick Start environment active, you can run the backend checks manually
in either setup:

```bash
make pre-commit         # Run on staged files
make pre-commit-all     # Run on all files
make format             # Auto-format code
make lint               # Run linters
make mypy               # Check types (baseline-driven)
```

#### Having issues?

- Check which [hook setup](#first-time-setup) this clone uses.
- `pre-commit validate-config .pre-commit-config.yaml` checks the backend hook
  configuration. The [setup checker](bin/verify-team-setup.sh) also looks for
  `.git/hooks` in the current directory, so its hook-installation check is not
  reliable from this subdirectory.

### Common Commands

```bash
# Code Quality
make pre-commit-install  # Standalone Python hook only (no custom Git hook path)
make pre-commit-all       # Run all pre-commit checks
make format              # Auto-format code
make lint                # Run linters
make check-all           # Run all quality checks

# Testing
make test                # Run all tests
make test-unit           # Tests marked unit
make test-integration    # Tests marked integration

# Django
DEBUG=true python manage.py runserver  # Start dev server (DEBUG defaults to off; set it explicitly to serve app static files and debug pages)
python manage.py migrate      # Run migrations
python manage.py test         # Run Django tests
```

### Contributing

Before submitting a PR:

1. Ensure pre-commit hooks are installed
2. Run `make pre-commit-all` to check your changes
3. Run `make test` to ensure tests pass
4. Follow the [PR template](../.github/PULL_REQUEST_TEMPLATE.md)

See [Contributing Guide](../CONTRIBUTING.md) for more details.

### Documentation

- 📚 [Backend Quick Start](#quick-start)
- 🔧 [Git hooks setup](#first-time-setup)
- 🔍 [Git hooks reference](../TESTING.md#git-hooks)
- 📖 [Testing guide](../TESTING.md)

---

# Pre-commit Quick Reference

For setup, follow the [Quick Start](#quick-start) and choose the
[hook setup](#first-time-setup) for your clone.

## Daily Usage

```bash
# Normal workflow - hooks run automatically
git add .
git commit -m "Your message"  # Hooks run here automatically

# Run manually on staged files
make pre-commit

# Run on all files
make pre-commit-all
```

## Common Commands

| Command | Description |
|---------|-------------|
| `make pre-commit-install` | Install the standalone Python hook only (no custom Git hook path) |
| `make pre-commit` | Run on staged files |
| `make pre-commit-all` | Run on all files |
| `make format` | Auto-fix and format code (`ruff check --fix`, `ruff format`) |
| `make lint` | Run linters (`ruff check`, `ruff format --check`) |
| `make mypy` | Compare type errors with the baseline |
| `make check-all` | Run all quality checks |
| `make test` | Run the backend tests |

## Quick Fixes

### "Command not found: pre-commit"
Follow the [Quick Start](#quick-start) to install and activate the developer environment.

### "Hooks failed"
1. Read the error message
2. Fix the issue or let hooks auto-fix
3. Run `git diff` to see changes
4. Commit again

### "Django check failed"
```bash
python manage.py check  # See details
python manage.py makemigrations  # If migrations needed
```

### "Secrets detected"
```bash
# Remove the secret, use environment variable instead
# If false positive:
detect-secrets scan > .secrets.baseline
```

## Skip Hooks (Emergency Only)

```bash
# Skip all (NOT RECOMMENDED)
git commit --no-verify

# Skip specific hook
SKIP=ruff-check,ruff-format git commit -m "Message"
```

## Installed Hooks

For the standalone Python hook, see the [backend hook configuration](.pre-commit-config.yaml)
for the enabled Ruff and file checks. Husky runs the [repository hooks](../TESTING.md#git-hooks) instead.

## Troubleshooting

```bash
# Standalone Python hook only (no custom Git hook path): clear cache and reinstall
pre-commit clean
pre-commit install --install-hooks

# Check the current pinned hook configuration
pre-commit validate-config .pre-commit-config.yaml

# Run with verbose output
pre-commit run --all-files --verbose
```

## Help

- 📚 [Git hooks setup](#first-time-setup)
- 🔧 [Configuration](.pre-commit-config.yaml)
- 💬 Ask in team Slack channel
