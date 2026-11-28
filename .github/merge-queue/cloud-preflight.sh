#!/usr/bin/env bash
set -euo pipefail
: "${BASE_OID:?need the upstream base commit}"
[[ "$BASE_OID" =~ ^[0-9a-f]{40}$ ]]
status_output="$(git status --porcelain --untracked-files=all --ignore-submodules=none)"
if [[ -n "$status_output" ]]; then
  echo 'Preflight requires a clean candidate checkout.' >&2
  exit 1
fi
base="$(git merge-base "$BASE_OID" HEAD)"
git diff --check "$base" HEAD

tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
git show "$BASE_OID:scripts/check_python_lint_delta.py" > "$tmp/lint.py"
git show "$BASE_OID:scripts/check_test_integrity.py" > "$tmp/integrity.py"
ruff_pin="$(git show "$BASE_OID:.github/workflows/python-lint.yml" | sed -nE 's/^  RUFF_VERSION: "([0-9]+\.[0-9]+\.[0-9]+)"[[:space:]]*(#.*)?$/\1/p')"
[[ "$ruff_pin" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
python3 -I -m venv "$tmp/venv"
"$tmp/venv/bin/python" -I -m pip install "ruff==$ruff_pin"
python3 -I "$tmp/lint.py" --base "$base" --config-ref "$BASE_OID" \
  --ruff "$tmp/venv/bin/ruff" --expect-ruff-version "$ruff_pin"
python3 -I "$tmp/integrity.py" --base "$base"
