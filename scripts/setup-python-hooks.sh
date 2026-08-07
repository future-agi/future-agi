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
#   2. Installs the hooks defined in .pre-commit-config.yaml
#   3. Verifies the installation

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Checking pre-commit..."
if ! command -v pre-commit &> /dev/null; then
    echo "    pre-commit not found. Installing with pip..."
    pip install pre-commit
fi

echo "==> Installing pre-commit hooks..."
pre-commit install

echo "==> Running hooks on all files (dry-run to verify)..."
pre-commit run --all-files || true

echo ""
echo "Python pre-commit hooks are now installed."
echo "They will run automatically via .husky/pre-commit on every git commit."
echo "To bypass: git commit --no-verify"
echo "To update: pre-commit autoupdate"
