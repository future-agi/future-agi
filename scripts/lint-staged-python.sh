#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/futureagi"

files=()
for file in "$@"; do
  case "${file}" in
    "${ROOT_DIR}"/*) rel="${file#"${ROOT_DIR}/"}" ;;
    *) rel="${file}" ;;
  esac

  [[ "${rel}" == futureagi/* ]] || continue
  [[ "${rel}" == *.py ]] || continue
  [[ "${rel}" == */migrations/* ]] && continue
  [[ -f "${ROOT_DIR}/${rel}" ]] || continue

  files+=("${rel#futureagi/}")
done

if (( ${#files[@]} == 0 )); then
  exit 0
fi

cd "${BACKEND_DIR}"

# Pinned to the version CI uses (RUFF_VERSION in .github/workflows/python-lint.yml;
# scripts/test_check_python_lint_delta.py checks they match), so a commit that
# passes here also passes there. Fall back to `uvx` when `uv run` cannot resolve
# it, for example on a fresh clone.
RUFF_VERSION="0.17.3"
if uv run --quiet --with "ruff==${RUFF_VERSION}" ruff --version >/dev/null 2>&1; then
  ruff_cmd=(uv run --quiet --with "ruff==${RUFF_VERSION}" ruff)
else
  ruff_cmd=(uvx "ruff@${RUFF_VERSION}")
fi

"${ruff_cmd[@]}" check --fix -- "${files[@]}"
"${ruff_cmd[@]}" format -- "${files[@]}"
