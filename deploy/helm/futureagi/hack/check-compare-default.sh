#!/usr/bin/env bash
set -euo pipefail
chart=$(cd "$(dirname "$0")/.." && pwd)
render=("${HELM:-helm}" template test "$chart" -f "$chart/examples/bundled.yaml")
check() {
  local expected=$1 workload value; shift
  for workload in backend worker; do
    value=$("${render[@]}" --show-only "templates/$workload/deployment.yaml" "$@" |
      awk '/name: COMPARE_BUCKET_NAME$/{getline; print $2}' | sort -u)
    [[ $value == "\"$expected\"" ]] || { printf '%s expected %s, got %s\n' "$workload" "$expected" "$value" >&2; return 1; }
  done
}
check shared-compare --set config.extraEnv.UPLOAD_BUCKET_NAME=shared
check local-compare --set config.extraEnv.UPLOAD_BUCKET_NAME=shared --set backend.extraEnv.UPLOAD_BUCKET_NAME=local
check explicit --set backend.extraEnv.UPLOAD_BUCKET_NAME=local --set objectStorage.compareBucket=explicit
check overridden --set objectStorage.compareBucket=explicit --set backend.extraEnv.COMPARE_BUCKET_NAME=overridden
check same --set backend.extraEnv.COMPARE_BUCKET_NAME=same --set worker.extraEnv.COMPARE_BUCKET_NAME=same
for scope in 'worker.extraEnv' 'worker.queues[0].extraEnv'; do
  if error=$("${render[@]}" --set 'worker.queues[0].name=compare' --set objectStorage.compareBucket=private \
    --set "$scope.COMPARE_BUCKET_NAME=other" 2>&1 >/dev/null); then
    printf 'Rendered conflicting %s.COMPARE_BUCKET_NAME\n' "$scope" >&2
    exit 1
  fi
  [[ $error == *'COMPARE_BUCKET_NAME must match'* ]]
done
printf 'API and workers share the effective comparison bucket; conflicting worker and queue overrides rejected\n'
