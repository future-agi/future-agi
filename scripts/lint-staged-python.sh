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
uv run ruff check --fix -- "${files[@]}"
uv run ruff format -- "${files[@]}"
