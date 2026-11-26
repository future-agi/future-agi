# Makefile for core-backend

.PHONY: help test test-unit test-integration test-watch test-shell clean lint format mypy mypy-baseline-sync pre-commit-install pre-commit pre-commit-all check-all

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "Tests (uses bin/test):"
	@echo "  test            Run all tests"
	@echo "  test-unit       Run unit tests"
	@echo "  test-integration Run integration tests"
	@echo "  test-watch      Watch mode"
	@echo "  test-shell      Open test shell"
	@echo ""
	@echo "Code Quality:"
	@echo "  lint            Run linters"
	@echo "  format          Format code"
	@echo "  mypy            Whole-repo mypy; fails on errors not in mypy-baseline.txt"
	@echo "  mypy-baseline-sync  Rewrite mypy-baseline.txt from a whole-repo mypy run"
	@echo "  check-all       Run lint + mypy + tests (pre-PR gate)"
	@echo ""
	@echo "Pre-commit hooks (uses .pre-commit-config.yaml):"
	@echo "  pre-commit-install  Install git hooks (one-time)"
	@echo "  pre-commit          Run hooks on staged files"
	@echo "  pre-commit-all      Run hooks on all files"
	@echo ""
	@echo "Or use bin/test directly for more options:"
	@echo "  bin/test help"

# Tests - delegate to bin/test
test:
	@bin/test

test-unit:
	@bin/test unit

test-integration:
	@bin/test integration

test-watch:
	@bin/test watch

test-shell:
	@bin/test shell

# Code Quality
# The same ruff as CI and the lint-staged hook: RUFF_VERSION is pinned in
# .github/workflows/python-lint.yml and scripts/lint-staged-python.sh too, and
# scripts/test_check_python_lint_delta.py checks they match. Override RUFF to
# use another binary, e.g. `make lint RUFF=ruff`.
RUFF_VERSION := 0.17.3
RUFF ?= uv run --quiet --with "ruff==$(RUFF_VERSION)" ruff
# CI checks only Python files. Without this, ruff 0.17 would also check and
# format notebooks, and format the Python code blocks in Markdown files.
RUFF_PYTHON_ONLY := --config "extend-exclude = ['*.md', '*.ipynb']"

# Both commands always run, and the target fails if either one does. `ruff check`
# reports the whole tree, so old violations show up as well as new ones (CI
# fails only on new ones; see scripts/check_python_lint_delta.py).
lint:
	@$(RUFF) check $(RUFF_PYTHON_ONLY) . ; status=$$?; \
	$(RUFF) format --check $(RUFF_PYTHON_ONLY) . || status=$$?; exit $$status

# `ruff check --fix` exits non-zero while unfixable violations remain, so the
# formatter runs regardless; the target still fails if either command does.
format:
	@$(RUFF) check --fix $(RUFF_PYTHON_ONLY) . ; status=$$?; \
	$(RUFF) format $(RUFF_PYTHON_ONLY) . || status=$$?; exit $$status

# Whole-repo mypy compared with mypy-baseline.txt, as CI's "Mypy (baseline)"
# job does (scripts/mypy_baseline.py). mypy's output is kept in
# .mypy_cache/mypy-output.txt. CI's environment is the reference: with the
# private ee/cloud checked out, expect extra errors under ee/cloud/ and in
# files that import it.
#
# Both targets run the mypy and stubs locked in uv.lock from this directory's
# .venv (uv sync --frozen --extra dev). EE_LICENSE_KEY=mypy-placeholder is not a
# key: any non-empty value makes tfc.settings.settings register ee.falcon_ai, as
# in CI's run (see [tool.django-stubs] in pyproject.toml).
mypy:
	@test -x .venv/bin/mypy || { echo "No .venv/bin/mypy here; run: uv sync --frozen --extra dev" >&2; exit 2; }
	@mkdir -p .mypy_cache; status=0; EE_LICENSE_KEY=mypy-placeholder .venv/bin/mypy . > .mypy_cache/mypy-output.txt || status=$$?; \
	python3 -I ../scripts/mypy_baseline.py filter --mypy-exit-code "$$status" --input .mypy_cache/mypy-output.txt

# Rewrite mypy-baseline.txt from a whole-repo run. Refused with ee/cloud
# present, because CI checks without it. To sync from CI's run instead,
# download the mypy-output artifact of the "Python types (advisory)" workflow
# and run: python3 -I ../scripts/mypy_baseline.py sync --input mypy-output.txt
mypy-baseline-sync:
	@test -x .venv/bin/mypy || { echo "No .venv/bin/mypy here; run: uv sync --frozen --extra dev" >&2; exit 2; }
	@if [ -e ee/cloud ]; then \
		echo "ee/cloud is checked out; the baseline is built without it. Sync from CI's mypy-output artifact instead." >&2; \
		exit 2; \
	fi
	@mkdir -p .mypy_cache; status=0; EE_LICENSE_KEY=mypy-placeholder .venv/bin/mypy . > .mypy_cache/mypy-output.txt || status=$$?; \
	python3 -I ../scripts/mypy_baseline.py sync --mypy-exit-code "$$status" --input .mypy_cache/mypy-output.txt

# Pre-commit hooks - delegate to the pre-commit tool (.pre-commit-config.yaml).
# pre-commit is pinned in requirements-test.txt / pyproject.toml.
pre-commit-install:
	@pre-commit install

pre-commit:
	@pre-commit run

pre-commit-all:
	@pre-commit run --all-files

# Aggregate quality gate referenced by CONTRIBUTING.md ("make check-all").
check-all: lint mypy test

# Cleanup
clean:
	@rm -rf htmlcov/ .pytest_cache/ .coverage test-reports/
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@bin/test down 2>/dev/null || true
