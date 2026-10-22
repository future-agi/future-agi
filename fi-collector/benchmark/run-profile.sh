#!/usr/bin/env bash
# Uses an ALREADY provisioned isolated stack. Never creates/stops containers and
# cannot change the collector's settings: it states the settings a profile needs
# and checks them against the collector's /healthz when BENCH_COLLECTOR_HEALTH_URL
# is set. Offline unless the caller explicitly adds --execute; large profiles also
# need --confirm-load "$BENCH_RUN_ID", and remote hosts need separate approval flags.
set -euo pipefail
profile="${1:-smoke}"
if (($#)); then shift; fi
: "${BENCH_PROJECT_ID:?Set the dedicated test project UUID}"
: "${BENCH_PROJECT_NAME:?Set its collector-resolvable project name}"
: "${BENCH_RUN_ID:?Set a new 16-character lowercase hexadecimal run ID}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The system under test is the collector/backend deployment, NOT this checkout.
# Its revision comes from the collector's /healthz build when reachable, else
# from BENCH_SUT_REVISION (BENCH_REVISION is accepted for older runbooks).
sut_revision="${BENCH_SUT_REVISION:-${BENCH_REVISION:-}}"
generator_revision="$(git -C "$root" rev-parse HEAD 2>/dev/null || true)"

# Per-profile load shape. Collector budget requirement: see "Collector settings"
# in benchmark/README.rst; 'otlpbench collector-budget' prints the exact value.
case "$profile" in
  smoke)
    rate=100 batch=100 concurrency=2
    load=(--rate "$rate" --duration 5s --max-duration 1m --max-bytes 16777216
      --batch-size "$batch" --concurrency "$concurrency")
    verify=() ;;
  sustained)
    rate=15000 batch=500 concurrency=16
    load=(--rate "$rate" --duration 24h --max-duration 24h --max-bytes 7000000000000
      --batch-size "$batch" --concurrency "$concurrency" --settle 2m)
    # Verification of 1.296e9 spans: 1M-span chunks, 5-minute chunk queries and a
    # 12-hour budget. These are unmeasured allowances, not a measured cost.
    verify=(--verify-chunk 1000000 --verify-query-timeout 5m --verify-timeout 12h --verify-max-memory 4294967296
      --direct-probe-interval 10s --direct-probe-min-age 30s --direct-probe-after-settle 20) ;;
  burst)
    rate=60000 batch=500 concurrency=32
    load=(--rate "$rate" --duration 60s --max-duration 1m --max-bytes 24000000000
      --batch-size "$batch" --concurrency "$concurrency" --settle 2m)
    verify=(--verify-chunk 1000000 --verify-query-timeout 2m --verify-timeout 1h --verify-max-memory 4294967296
      --direct-probe-interval 2s --direct-probe-min-age 10s --direct-probe-after-settle 20) ;;
  *) echo 'profile must be smoke, sustained or burst' >&2; exit 2 ;;
esac

args=(
  --run-id "$BENCH_RUN_ID"
  --project-id "$BENCH_PROJECT_ID" --project-name "$BENCH_PROJECT_NAME"
  --endpoint "${BENCH_OTLP_URL:-http://127.0.0.1:4318/v1/traces}"
  --encoding "${BENCH_ENCODING:-protobuf}"
  --ch-url "${BENCH_CH_URL:-http://127.0.0.1:8123}"
  --database "${BENCH_DATABASE:-default}" --table "${BENCH_TABLE:-spans}"
  --environment "${BENCH_ENVIRONMENT:-isolated-local}"
  --topology "${BENCH_TOPOLOGY:-single-node}"
  --generator-revision "$generator_revision"
  --profile-label "$profile" --payload "${BENCH_PAYLOAD:-mixed}"
  "${load[@]}" "${verify[@]}"
)
if [[ -n "$sut_revision" ]]; then args+=(--revision "$sut_revision"); fi
if [[ "${BENCH_GZIP:-0}" == 1 ]]; then args+=(--gzip); fi
if [[ -n "${BENCH_SEED:-}" ]]; then args+=(--seed "$BENCH_SEED"); fi
if [[ -n "${BENCH_VERIFY_CHECKPOINT:-}" ]]; then args+=(--verify-checkpoint "$BENCH_VERIFY_CHECKPOINT"); fi
if [[ -n "${BENCH_OTLP_HEADERS:-}" ]]; then args+=(--otlp-headers-env BENCH_OTLP_HEADERS); fi
if [[ -n "${BENCH_CH_HEADERS:-}" ]]; then args+=(--ch-headers-env BENCH_CH_HEADERS); fi

budget=(--rate "$rate" --batch-size "$batch" --concurrency "$concurrency")
if [[ -n "${BENCH_COLLECTOR_HEALTH_URL:-}" ]]; then
  args+=(--collector-health-url "$BENCH_COLLECTOR_HEALTH_URL")
  budget+=(--collector-health-url "$BENCH_COLLECTOR_HEALTH_URL")
fi
if [[ -n "${BENCH_ALLOW_HOSTS:-}" ]]; then
  args+=(--allow-hosts "$BENCH_ALLOW_HOSTS")
  budget+=(--allow-hosts "$BENCH_ALLOW_HOSTS")
fi
if [[ -n "${BENCH_COLLECTOR_MAX_PENDING_ROWS:-}" ]]; then
  args+=(--declared-collector-max-pending-rows "$BENCH_COLLECTOR_MAX_PENDING_ROWS")
  budget+=(--declared-collector-max-pending-rows "$BENCH_COLLECTOR_MAX_PENDING_ROWS")
fi

cd "$root"
cat >&2 <<EOF
== Profile '$profile': $rate spans/s, batch $batch, $concurrency in flight ==
Collector settings this profile needs (set them on the collector under test;
this script cannot change them):
  * FI_MAX_PENDING_ROWS (server.max_pending_rows) at least the required_pending_rows
    printed below. The collector default is 20000, which the sustained and burst
    profiles exceed; an undersized budget turns load into HTTP 503 / gRPC
    RESOURCE_EXHAUSTED whole-request rejections that the result counts separately.
  * batch size $batch <= max_pending_rows, or every request is refused as
    non-retryable (HTTP 413 / gRPC INVALID_ARGUMENT).
  * FI_BUILD_REVISION (or a VCS-stamped build) so /healthz names the revision.
EOF
if [[ -z "${BENCH_COLLECTOR_HEALTH_URL:-}" ]]; then
  echo "*** WARNING: BENCH_COLLECTOR_HEALTH_URL is unset: the collector's real budget and revision cannot be checked ***" >&2
fi
if [[ "$profile" != smoke && -z "${BENCH_VERIFY_CHECKPOINT:-}" ]]; then
  echo "*** WARNING: BENCH_VERIFY_CHECKPOINT is unset: an interrupted verification of this profile cannot be resumed ***" >&2
fi
# Read-only GET of /healthz only when BENCH_COLLECTOR_HEALTH_URL is set; otherwise
# offline. Warnings go to stderr; the JSON check to stdout.
go run ./cmd/otlpbench collector-budget "${budget[@]}"
# Arguments are passed as an array, never evaluated as a shell command.
exec go run ./cmd/otlpbench "${args[@]}" "$@"
