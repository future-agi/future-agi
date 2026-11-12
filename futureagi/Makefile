# Makefile for core-backend

.PHONY: help test test-unit test-integration test-watch test-shell clean lint format mypy mypy-baseline pre-commit-install pre-commit pre-commit-all check-all

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
	@echo "  mypy            Type checking (baseline-driven)"
	@echo "  mypy-baseline   Regenerate the mypy baseline"
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

mypy:
	@./bin/check_mypy.sh

mypy-baseline:
	@./bin/generate_mypy_baseline.sh

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
