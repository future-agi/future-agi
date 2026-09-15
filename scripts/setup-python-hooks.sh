#!/usr/bin/env bash
# Install Python pre-commit hooks for Future AGI.
#
# Usage:  ./scripts/setup-python-hooks.sh
#
# Prerequisites:
#   - Python 3.11+ with pip
#   - pre-commit (pip install pre-commit)
#
# What it does:
#   1. Installs pre-commit if missing
#   2. Verifies the hooks defined in .pre-commit-config.yaml run correctly
#
# Note: we intentionally do NOT run `pre-commit install`. The repo uses husky
# v9 (package.json "prepare": "husky"), which sets core.hooksPath=.husky/.
# With that set, `pre-commit install` refuses to install ("Cowardly refusing
# to install hooks with core.hooksPath set"). The .husky/pre-commit hook is
# the thing that invokes `pre-commit run`, so no separate install is needed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Checking pre-commit..."
if ! command -v pre-commit &> /dev/null; then
    echo "    pre-commit not found. Installing with pip..."
    pip install pre-commit
fi

echo "==> Running hooks on all files (dry-run to verify)..."
pre-commit run --all-files || true

echo ""
echo "Python pre-commit hooks are now installed."
echo "They will run automatically via .husky/pre-commit on every git commit."
echo "To bypass: git commit --no-verify"
echo "To update: pre-commit autoupdate"
