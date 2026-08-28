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

# Prefer a project-local ruff (uv-managed venv or dev group). Fall back to
# `uvx ruff` when the current env doesn't ship one — this happens on fresh
# clones or when the developer hasn't run `uv sync --group dev`.
if uv run --quiet --with ruff ruff --version >/dev/null 2>&1; then
  ruff_cmd=(uv run --quiet --with ruff ruff)
else
  ruff_cmd=(uvx ruff)
fi

"${ruff_cmd[@]}" check --fix -- "${files[@]}"
"${ruff_cmd[@]}" format -- "${files[@]}"
