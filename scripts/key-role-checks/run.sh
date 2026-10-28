#!/usr/bin/env bash
# Run only on one disposable cloud VM. No production services or credentials.
set -euo pipefail
repo=$(realpath "$1")
base=$2
red=$3
dev=$4
head=$5
if [[ $(uname -s) != Linux || ${OMEGA_EXEC_SOURCE_HEAD:-} != "$head" ]]; then
  printf '%s\n' 'This driver requires the pinned cloud-exec snapshot.' >&2
  exit 2
fi
scripts="$repo/scripts/key-role-checks"
run="$repo/.key-role-checks/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$run/raw" "$run/results"
printf '%s\n' "git:$base" "git:$red" "git:$head" "git:$dev" > "$run/results/revisions.txt"

# Detached, new checkouts only: the shipped tree and every existing branch stay intact.
for label in base red head dev; do
  case "$label" in
    base) revision=$base;; red) revision=$red;; head) revision=$head;; dev) revision=$dev;;
  esac
  git -C "$repo" worktree add --detach "$run/$label" "$revision"
done
git -C "$repo" worktree add -b "verify/key-role-replay-$(date -u +%Y%m%dT%H%M%SZ)-$$" "$run/replay" "$dev"
# Applying explicit tree deltas works even when cloud exec ships shallow commit objects.
git -C "$repo" diff --binary "$base" "$red" > "$run/tests.patch"
git -C "$run/replay" apply --check "$run/tests.patch"
git -C "$run/replay" apply --index "$run/tests.patch"
HUSKY=0 git -C "$run/replay" -c user.name='Key role checks' -c user.email='checks@example.test' commit \
  -m 'test(accounts): replay key role contracts' -m 'Co-Authored-By: Claude Code <noreply@anthropic.com>'
git -C "$repo" diff --binary "$red" "$head" > "$run/policy.patch"
git -C "$run/replay" apply --check "$run/policy.patch"
git -C "$run/replay" apply --index "$run/policy.patch"
HUSKY=0 git -C "$run/replay" -c user.name='Key role checks' -c user.email='checks@example.test' commit \
  -m 'fix(accounts): replay organization key role policy' -m 'Co-Authored-By: Claude Code <noreply@anthropic.com>'
printf 'git:%s\n' "$(git -C "$run/replay" rev-parse HEAD)" >> "$run/results/revisions.txt"
test -z "$(git -C "$run/replay" status --porcelain)"

# This is the repository's disposable Postgres/Redis/ClickHouse/MinIO test stack.
# Its services have no external application credentials. Python tests get only
# loopback endpoints and the socket audit hook is installed before Django loads.
compose=(docker compose -p key-role-checks -f "$repo/futureagi/docker-compose.test.yml")
"${compose[@]}" up -d --wait
trap '"${compose[@]}" down' EXIT
for label in base red head dev replay; do
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
# Both complete accounts suites plus the sibling tools use identical test services.
run_suite base_accounts base accounts/tests ai_tools/tests/test_user_tools.py
run_suite head_accounts head accounts/tests ai_tools/tests/test_user_tools.py
run_suite dev_accounts dev accounts/tests ai_tools/tests/test_user_tools.py
run_suite replay_accounts replay accounts/tests ai_tools/tests/test_user_tools.py
python3 -I "$scripts/check_results.py" "$run/raw" "$run/results"
