#!/usr/bin/env bash
set -euo pipefail
case "$PROFILE" in
  baseline|overlap) ;;
  tuned) export UV_COMPILE_BYTECODE=false UV_LINK_MODE=hardlink ;;
  *) echo 'Unknown backend setup profile' >&2; exit 2 ;;
esac
compose() {
  local started=$SECONDS status=0
  docker compose -f docker-compose.test.yml -p futureagi-test up -d --wait --wait-timeout 180 || status=$?
  printf 'Service startup and health: %ss (exit %s)\n' "$((SECONDS-started))" "$status"
  return "$status"
}
if [[ $PROFILE != baseline ]]; then
  # Same shell owns the only `up`, so wait returns its actual exit status.
  compose > "$RUNNER_TEMP/compose-health.log" 2>&1 &
  service_pid=$!
fi
sync_start=$SECONDS
sync_status=0
uv sync --frozen --all-extras || sync_status=$?
sync_seconds=$((SECONDS - sync_start))
health_status=0
if [[ $PROFILE == baseline ]]; then
  compose > "$RUNNER_TEMP/compose-health.log" 2>&1 || health_status=$?
else
  wait "$service_pid" || health_status=$?
fi
cat "$RUNNER_TEMP/compose-health.log"
if (( sync_status || health_status )); then
  printf 'dependency sync=%d service health=%d\n' "$sync_status" "$health_status" >&2
  exit 1
fi
# No later `uv run` may resync away the extras or shift compilation into an
# unmeasured step. Leave production uv.toml and all non-shard builds alone.
printf 'UV_PYTHON=%s\nUV_NO_SYNC=true\n' "$UV_PYTHON" >> "$GITHUB_ENV"
import_start=$SECONDS
.venv/bin/python -c 'import sys, django, pytest; assert sys.version_info[:3] == (3, 12, 3)'
printf 'Setup profile: %s; uv cache hit: %s; sync %ss; first Django/pytest import %ss; restore through first import %ss.\n' \
  "$PROFILE" "${UV_CACHE_HIT:-false}" "$sync_seconds" "$((SECONDS-import_start))" "$(( $(date +%s) - $(cat "$RUNNER_TEMP/backend-setup-start") ))" >> "$GITHUB_STEP_SUMMARY"
