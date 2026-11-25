#!/usr/bin/env bash
set -euo pipefail
chart=$(cd "$(dirname "$0")/.." && pwd)
check() {
  local expected=$1; shift
  # Inspect only the backend Deployment's COMPARE_BUCKET_NAME literal.
  local value
  value=$("${HELM:-helm}" template test "$chart" -f "$chart/examples/bundled.yaml" \
    --show-only templates/backend/deployment.yaml "$@" | awk '/name: COMPARE_BUCKET_NAME$/{getline; print $2}' | sort -u)
  [[ $value == "\"$expected\"" ]] || { printf 'Expected %s, got %s\n' "$expected" "$value" >&2; return 1; }
}
check shared-compare --set config.extraEnv.UPLOAD_BUCKET_NAME=shared
check local-compare --set config.extraEnv.UPLOAD_BUCKET_NAME=shared --set backend.extraEnv.UPLOAD_BUCKET_NAME=local
check explicit --set backend.extraEnv.UPLOAD_BUCKET_NAME=local --set objectStorage.compareBucket=explicit
check overridden --set objectStorage.compareBucket=explicit --set backend.extraEnv.COMPARE_BUCKET_NAME=overridden
printf 'Effective upload bucket default and explicit compare overrides passed\n'
