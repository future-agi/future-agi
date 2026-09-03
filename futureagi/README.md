# Core Backend

## Setup

### Initial Setup
```bash
git submodule update --init --recursive
```

### Type Checking with MyPy

**We use MyPy with a baseline-driven approach** - new code must be typed, but existing code is grandfathered in.

#### Quick Start

```bash
# One-time setup
make install-mypy
make mypy-baseline

# Before every commit
make mypy
```

**🔥 CI will reject PRs with new type errors!**

📚 **[Read the Full Guide](MYPY_SETUP_GUIDE.md)** | **[Detailed Docs](docs/MYPY_TYPE_CHECKING.md)**

### Pre-commit Hooks

**This project uses pre-commit hooks to ensure code quality.** They run automatically before each commit.

#### What do they check?

- ✅ Code formatting (Black, isort)
- ✅ Linting (Ruff)
- ✅ Security (Bandit, detect-secrets)
- ✅ Django checks (migrations, system checks)
- ✅ Type checking (mypy)
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
- See [MyPy Setup Guide](MYPY_SETUP_GUIDE.md)
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
python manage.py runserver    # Start dev server
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
# (Hooks are wired through husky, so no `pre-commit install` is needed)
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
| `make format` | Auto-format code (black + isort) |
| `make lint` | Run linters (ruff, black, isort) |
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
SKIP=ruff git commit -m "Message"
```

## Installed Hooks

### Auto-fixing Hooks
✅ **black** - Python formatter
✅ **isort** - Import sorter
✅ **ruff** - Linter with auto-fix
✅ **django-upgrade** - Upgrade Django code
✅ **djlint** - Django template formatter

### Checking Hooks
🔍 **mypy** - Type checking
🔍 **bandit** - Security scan
🔍 **detect-secrets** - Secret detection
🔍 **django-check** - Django system checks
🔍 **check-yaml/json** - File validation

## Troubleshooting

```bash
# Clear cache and reinstall
pre-commit clean
pre-commit install-hooks

# Update hooks to latest versions
make pre-commit-update

# Run with verbose output
pre-commit run --all-files --verbose
```

## Help

- 📚 [Full Setup Guide](PRE_COMMIT_SETUP.md)
- 🔧 [Configuration](../.pre-commit-config.yaml)
- 💬 Ask in team Slack channel
