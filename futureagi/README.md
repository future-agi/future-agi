# Core Backend

## Setup

### Initial Setup
```bash
git submodule update --init --recursive
```

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
baseline. See `scripts/mypy_baseline.py`.

### Pre-commit Hooks

**This project uses pre-commit hooks to ensure code quality.** They run automatically before each commit.

#### What do they check?

- ✅ Code formatting and import sorting (Ruff)
- ✅ Linting (Ruff)
- ✅ Security (Bandit, detect-secrets)
- ✅ Django checks (migrations, system checks)
- ✅ File validation (YAML, JSON)

#### First-time setup

```bash
# Install hooks (one-time)
make pre-commit-install

# Run on all files (first time)
make pre-commit-all
```

#### Daily usage

Pre-commit runs automatically on `git commit`. You can also run manually:

```bash
make pre-commit         # Run on staged files
make pre-commit-all     # Run on all files
make format             # Auto-format code
make lint               # Run linters
make mypy               # Check types (baseline-driven)
```

#### Having issues?

- See [Pre-commit Setup Guide](docs/PRE_COMMIT_SETUP.md)
- See [Quick Reference](docs/PRE_COMMIT_QUICK_REFERENCE.md)
- Run `./scripts/verify-team-setup.sh` to diagnose issues

### Common Commands

```bash
# Code Quality
make pre-commit-install   # Install pre-commit hooks
make pre-commit-all       # Run all pre-commit checks
make format              # Auto-format code
make lint                # Run linters
make check-all           # Run all quality checks

# Testing
make test                # Run all tests
make test-coverage       # Tests with coverage
make test-backend-only   # Backend tests only

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
4. Follow the [PR template](.github/PULL_REQUEST_TEMPLATE.md)

See [Contributing Guide](CONTRIBUTING.md) for more details.

### Documentation

- 📚 [Pre-commit Setup Guide](docs/PRE_COMMIT_SETUP.md)
- 📋 [Onboarding Checklist](docs/ONBOARDING_CHECKLIST.md)
- 🔍 [Quick Reference](docs/PRE_COMMIT_QUICK_REFERENCE.md)
- 📖 [Testing Guide](TESTING.md)

---

## Alternative Minimal Version

If you want a shorter version:

---

## Development Setup

### Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Install pre-commit hooks (MANDATORY)
make pre-commit-install

# Verify setup
./scripts/verify-team-setup.sh

# Start developing!
```

### Pre-commit Hooks

This project uses pre-commit hooks to ensure code quality. They run automatically on commit.

**Setup (one-time):** `make pre-commit-install`

**Documentation:** [Pre-commit Guide](docs/PRE_COMMIT_SETUP.md)

---


# Pre-commit Quick Reference

Quick reference for pre-commit hooks usage.

## One-Time Setup

```bash
# Recommended: Use the setup script
make pre-commit-install

# Or manually
pip install pre-commit
pre-commit install
```

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
| `make pre-commit-install` | Install pre-commit hooks (one-time) |
| `make pre-commit` | Run on staged files |
| `make pre-commit-all` | Run on all files |
| `make format` | Auto-fix and format code (`ruff check --fix`, `ruff format`) |
| `make lint` | Run linters (`ruff check`, `ruff format --check`) |
| `make check-secrets` | Check for secrets |
| `make check-all` | Run all quality checks |
| `make fix` | Auto-fix common issues |

## Quick Fixes

### "Command not found: pre-commit"
```bash
pip install pre-commit
```

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

### Auto-fixing Hooks
✅ **ruff-check** - Linter and import sorter, with auto-fix
✅ **ruff-format** - Python formatter
✅ **django-upgrade** - Upgrade Django code
✅ **djlint** - Django template formatter

### Checking Hooks
🔍 **bandit** - Security scan
🔍 **detect-secrets** - Secret detection
🔍 **django-check** - Django system checks
🔍 **check-yaml/json** - File validation

## Troubleshooting

```bash
# Clear cache and reinstall
pre-commit clean
pre-commit install --install-hooks

# Update hooks to latest versions
make pre-commit-update

# Run with verbose output
pre-commit run --all-files --verbose
```

## Help

- 📚 [Full Setup Guide](PRE_COMMIT_SETUP.md)
- 🔧 [Configuration](.pre-commit-config.yaml)
- 💬 Ask in team Slack channel
