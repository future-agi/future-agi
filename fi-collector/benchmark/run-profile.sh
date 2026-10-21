#!/usr/bin/env bash
# Uses an ALREADY provisioned isolated stack. Never creates/stops containers.
# Offline unless the caller explicitly adds --execute; large profiles also need
# --confirm-load "$BENCH_RUN_ID", and remote hosts need separate approval flags.
set -euo pipefail
profile="${1:-smoke}"
if (($#)); then shift; fi
: "${BENCH_PROJECT_ID:?Set the dedicated test project UUID}"
: "${BENCH_PROJECT_NAME:?Set its collector-resolvable project name}"
: "${BENCH_RUN_ID:?Set a new 16-character lowercase hexadecimal run ID}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
args=(
  --run-id "$BENCH_RUN_ID"
  --project-id "$BENCH_PROJECT_ID" --project-name "$BENCH_PROJECT_NAME"
  --endpoint "${BENCH_OTLP_URL:-http://127.0.0.1:4318/v1/traces}"
  --ch-url "${BENCH_CH_URL:-http://127.0.0.1:8123}"
  --database "${BENCH_DATABASE:-default}" --table "${BENCH_TABLE:-spans}"
  --environment "${BENCH_ENVIRONMENT:-isolated-local}"
  --topology "${BENCH_TOPOLOGY:-single-node}"
  --revision "${BENCH_REVISION:-$(git -C "$root" rev-parse HEAD)}"
  --profile-label "$profile" --payload mixed
)
case "$profile" in
  smoke) args+=(--rate 100 --duration 5s --max-duration 1m --max-bytes 16777216) ;;
  sustained) args+=(--rate 15000 --duration 24h --max-duration 24h --max-bytes 7000000000000 --batch-size 500 --concurrency 16 --settle 2m --verify-chunk 1000000) ;;
  burst) args+=(--rate 60000 --duration 60s --max-duration 1m --max-bytes 24000000000 --batch-size 500 --concurrency 32 --settle 2m --verify-chunk 1000000) ;;
  *) echo 'profile must be smoke, sustained or burst' >&2; exit 2 ;;
esac
if [[ -n "${BENCH_OTLP_HEADERS:-}" ]]; then args+=(--otlp-headers-env BENCH_OTLP_HEADERS); fi
if [[ -n "${BENCH_CH_HEADERS:-}" ]]; then args+=(--ch-headers-env BENCH_CH_HEADERS); fi
# Arguments are passed as an array, never evaluated as a shell command.
cd "$root"
exec go run ./cmd/otlpbench "${args[@]}" "$@"
