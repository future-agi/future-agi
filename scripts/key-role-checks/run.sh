#!/usr/bin/env bash
# Run only on one disposable cloud VM. No production services or credentials.
set -euo pipefail
repo=$(realpath "$1")
base=$2
red=$3
dev=$4
head=$5
removal=$6
history=$7
if [[ $(uname -s) != Linux || ${OMEGA_EXEC_SOURCE_HEAD:-} != "$head" ]]; then
  printf '%s\n' 'This driver requires the pinned cloud-exec snapshot.' >&2
  exit 2
fi
scripts="$repo/scripts/key-role-checks"
run="$repo/.key-role-checks/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$run/raw" "$run/results"
printf '%s\n' "git:$base" "git:$red" "git:$head" "git:$dev" > "$run/results/revisions.txt"

# Detached, new checkouts only: the shipped tree and every existing branch stay intact.
for label in base red head dev removal history; do
  case "$label" in
    base) revision=$base;; red) revision=$red;; head) revision=$head;; dev) revision=$dev;;
    removal) revision=$removal;; history) revision=$history;;
  esac
  git -C "$repo" worktree add --detach "$run/$label" "$revision"
done
# The identical final history/principal contracts run on the pre-repair tree,
# including the lowercase-email correction for the legacy add-user control.
for test_file in test_membership_role_history.py test_system_key_principal.py test_key_management_scope.py; do
  cp "$run/head/futureagi/accounts/tests/$test_file" "$run/history/futureagi/accounts/tests/$test_file"
done
# Keep original bootstrap/management/tool red assertions identical to green too.
cp "$run/head/futureagi/accounts/tests/test_key_management_scope.py" "$run/red/futureagi/accounts/tests/test_key_management_scope.py"
# Use the exact final regression tests on the pre-repair implementation. This
# also carries corrections to positive controls (for example revoked sessions
# are 401, while a freshly issued bearer without membership is 403).
cp "$run/head/futureagi/accounts/tests/test_membership_key_healing.py" \
  "$run/removal/futureagi/accounts/tests/test_membership_key_healing.py"
git -C "$run/removal" add futureagi/accounts/tests/test_membership_key_healing.py
if ! git -C "$run/removal" diff --cached --quiet; then
  UV_NO_SYNC=1 git -C "$run/removal" -c user.name='Key role checks' -c user.email='checks@example.test' commit \
    -m 'test(accounts): pin bearer removal controls for paired verification' \
    -m 'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>'
fi
cmp "$run/head/futureagi/accounts/tests/test_membership_key_healing.py" \
  "$run/removal/futureagi/accounts/tests/test_membership_key_healing.py"
printf 'removal_base=git:%s\nremoval_tests=git:%s\n' "$removal" \
  "$(git -C "$run/removal" rev-parse HEAD)" >> "$run/results/revisions.txt"
git -C "$repo" worktree add -b "verify/key-role-replay-$(date -u +%Y%m%dT%H%M%SZ)-$$" "$run/replay" "$dev"
# Applying explicit tree deltas works even when cloud exec ships shallow commit objects.
git -C "$repo" diff --binary "$base" "$red" > "$run/tests.patch"
git -C "$run/replay" apply --check "$run/tests.patch"
git -C "$run/replay" apply --index "$run/tests.patch"
UV_NO_SYNC=1 git -C "$run/replay" -c user.name='Key role checks' -c user.email='checks@example.test' commit \
  -m 'test(accounts): replay key role contracts' -m 'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>'
git -C "$repo" diff --binary "$red" "$head" > "$run/policy.patch"
git -C "$run/replay" apply --check "$run/policy.patch"
git -C "$run/replay" apply --index "$run/policy.patch"
UV_NO_SYNC=1 git -C "$run/replay" -c user.name='Key role checks' -c user.email='checks@example.test' commit \
  -m 'fix(accounts): replay organization key role policy' -m 'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>'
printf 'git:%s\n' "$(git -C "$run/replay" rev-parse HEAD)" >> "$run/results/revisions.txt"
test -z "$(git -C "$run/replay" status --porcelain)"

# This is the repository's disposable Postgres/Redis/ClickHouse/MinIO test stack.
# Its services have no external application credentials. Python tests get only
# loopback endpoints and the socket audit hook is installed before Django loads.
compose=(docker compose -p key-role-checks -f "$repo/futureagi/docker-compose.test.yml")
"${compose[@]}" up -d --wait
trap '"${compose[@]}" down' EXIT
for label in base red head dev replay removal history; do
  (cd "$run/$label/futureagi" && uv sync --locked)
done

run_suite() {
  local label=$1 tree=$2
  shift 2
  local code=0
  env -i HOME="$HOME" PATH="$PATH" \
    OMEGA_EXEC_SOURCE_HEAD="$head" \
    DJANGO_SETTINGS_MODULE=tfc.settings.test ENV_TYPE=local TESTING=true \
    PG_HOST=127.0.0.1 PG_PORT=15432 PG_DB=test_tfc PG_TEST_DB="test_key_roles_$label" \
    PG_USER=test_user PG_PASSWORD=test_password \
    PGBOUNCER_HOST=127.0.0.1 PGBOUNCER_PORT=15432 \
    REDIS_URL=redis://127.0.0.1:16379/0 REDIS_LOCK_URL=redis://127.0.0.1:16379/2 \
    REDIS_CACHE_URL=redis://127.0.0.1:16379/3 \
    CH_HOST=127.0.0.1 CH_PORT=19000 CH_DATABASE=test_tfc CH_USERNAME=default CH_PASSWORD='' \
    CH25_HOST=127.0.0.1 CH25_HTTP_PORT=18123 CH25_TCP_PORT=19000 CH25_DATABASE=test_tfc \
    FI_CH_TEST_SIDECAR_NATIVE_PORT=19000 FI_CH_TEST_SIDECAR_HTTP_PORT=18123 \
    MINIO_ENDPOINT=127.0.0.1:19005 MINIO_URL=127.0.0.1:19005 \
    MINIO_ACCESS_KEY=minioadmin MINIO_SECRET_KEY=minioadmin \
    FUTURE_AGI_LICENSE_URL=http://127.0.0.1:9 \
    FUTURE_AGI_TELEMETRY_URL=http://127.0.0.1:9 FUTURE_AGI_TELEMETRY_DISABLED=true \
    MODEL_SERVING_URL=http://127.0.0.1:9 AWS_EC2_METADATA_DISABLED=true \
    KEY_CHECK_SOCKET_LOG="$run/results/$label-socket-guard.txt" \
    "$run/$tree/futureagi/.venv/bin/python" "$scripts/run_pytest.py" "$run/$tree/futureagi" \
      -q --tb=short -rA --create-db -p no:cacheprovider --junitxml="$run/raw/$label.xml" "$@" \
      > "$run/raw/$label.log" 2>&1 || code=$?
  printf '%s\n' "$code" > "$run/raw/$label.exit"
  printf '%s exit=%s\n' "$label" "$code"
}
focused=(accounts/tests/test_key_role_scope.py accounts/tests/test_key_management_scope.py accounts/tests/test_key_tool_scope.py)
run_suite red red "${focused[@]}"
run_suite green head "${focused[@]}"
run_suite removal_red removal accounts/tests/test_membership_key_healing.py
run_suite removal_green head accounts/tests/test_membership_key_healing.py
# The tests-first driver includes the RuntimeError-only negative probe.
for label in removal head; do
  code=0
  python3 -I "$run/$label/scripts/key-role-checks/test_driver.py" \
    > "$run/results/driver-$label.log" 2>&1 || code=$?
  printf '%s\n' "$code" > "$run/raw/driver-$label.exit"
done
history_tests=(accounts/tests/test_membership_role_history.py accounts/tests/test_system_key_principal.py accounts/tests/test_key_management_scope.py)
run_suite history_red history "${history_tests[@]}"
run_suite history_green head "${history_tests[@]}"
# Both complete accounts suites plus the sibling tools use identical test services.
run_suite base_accounts base accounts/tests ai_tools/tests/test_user_tools.py
run_suite head_accounts head accounts/tests ai_tools/tests/test_user_tools.py
run_suite dev_accounts dev accounts/tests ai_tools/tests/test_user_tools.py
run_suite replay_accounts replay accounts/tests ai_tools/tests/test_user_tools.py
python3 -I "$scripts/check_results.py" "$run/raw" "$run/results"
