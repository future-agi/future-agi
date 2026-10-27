#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v yarn >/dev/null 2>&1; then
    echo "yarn is required for repo hooks. Install Yarn 1.x, then rerun this script." >&2
    exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required for backend Python checks. Install uv, then rerun this script." >&2
    exit 1
fi

if [[ "${SKIP_INSTALL:-0}" != "1" ]]; then
    if [[ ! -d node_modules ]]; then
        yarn install --frozen-lockfile
    fi

    if [[ ! -d frontend/node_modules ]]; then
        yarn --cwd frontend install --frozen-lockfile
    fi

    if [[ ! -d futureagi/.venv ]]; then
        (cd futureagi && uv sync --dev)
    fi
fi

yarn prepare
npx --no-install lint-staged --allow-empty

echo "Commit checks are installed and verified."
