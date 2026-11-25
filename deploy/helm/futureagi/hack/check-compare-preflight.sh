#!/usr/bin/env bash
# Exercise external-storage validation entirely at render time.
set -euo pipefail
chart=$(cd "$(dirname "$0")/.." && pwd)
helm=${HELM:-helm}
for backend in s3 gcs; do
  args=(template test "$chart" -f "$chart/examples/external.yaml" --set "objectStorage.backend=$backend")
  if error=$("$helm" "${args[@]}" --set-string 'objectStorage.compareBucket=' 2>&1 >/dev/null); then
    printf 'External %s rendered without a configured comparison bucket\n' "$backend" >&2
    exit 1
  fi
  [[ $error == *objectStorage.compareBucket* && $error == *COMPARE_BUCKET_NAME* ]]
  "$helm" "${args[@]}" --set-string objectStorage.compareBucket=test-private >/dev/null
  printf '%s rejects missing configuration and accepts a configured private bucket\n' "$backend"
done
