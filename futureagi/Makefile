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
lint:
	@ruff check . --fix
	@black --check .

format:
	@black .
	@isort .

# Whole-repo mypy compared with mypy-baseline.txt, as CI's "Mypy (baseline)"
# job does (scripts/mypy_baseline.py). mypy's output is kept in
# .mypy_cache/mypy-output.txt. CI's environment is the reference: with the
# private ee/cloud checked out, expect extra errors under ee/cloud/ and in
# files that import it.
mypy:
	@mkdir -p .mypy_cache; status=0; mypy . > .mypy_cache/mypy-output.txt || status=$$?; \
	python3 -I ../scripts/mypy_baseline.py filter --mypy-exit-code "$$status" --input .mypy_cache/mypy-output.txt

# Rewrite mypy-baseline.txt from a whole-repo run. Refused with ee/cloud
# present, because CI checks without it. To sync from CI's run instead,
# download the mypy-output artifact of the "Python types (advisory)" workflow
# and run: python3 -I ../scripts/mypy_baseline.py sync --input mypy-output.txt
mypy-baseline-sync:
	@if [ -e ee/cloud ]; then \
		echo "ee/cloud is checked out; the baseline is built without it. Sync from CI's mypy-output artifact instead." >&2; \
		exit 2; \
	fi
	@mkdir -p .mypy_cache; status=0; mypy . > .mypy_cache/mypy-output.txt || status=$$?; \
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
