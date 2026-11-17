#!/usr/bin/env bash
# Run with bash bin/tests/test-build-local.sh. No Docker daemon or dependencies.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/futureagi-build-local.XXXXXX")"
trap 'rm -rf -- "$TMP"' EXIT
mkdir -p "$TMP/archive"
unset FUTURE_AGI_REVISION GIT_DIR GIT_WORK_TREE
HEAD_REVISION="$(git -C "$ROOT" rev-parse --verify -q HEAD)"
TEST_REVISION="$(printf '%040d' 1)"
DIM='' RESET=''

say() { :; }
ok() { :; }
die() { printf '%s\n' "$*" >&2; exit 64; }
log_only() { printf '%s\n' "$*" >> "$TEST_DIR/log"; }
docker() {
  # `image inspect` answers DOCKER_IMAGE_LOOKUP, `manifest inspect` DOCKER_MANIFEST_LOOKUP;
  # both default to DOCKER_LOOKUP (default: found).
  case "$1" in
    image) return "${DOCKER_IMAGE_LOOKUP:-${DOCKER_LOOKUP:-0}}" ;;
    manifest) return "${DOCKER_MANIFEST_LOOKUP:-${DOCKER_LOOKUP:-0}}" ;;
  esac
  BUILDS=$((BUILDS + 1))
  printf '%s\0' "$@" > "$TEST_DIR/build-$BUILDS"
}
# shellcheck source=../lib/build-local.sh
source "$ROOT/bin/lib/build-local.sh"

fail() { printf '  %s\n' "$*" >&2; exit 1; }
assert_build() {
  local number="$1" arg i=0
  shift
  local -a actual=() expected=("$@")
  while IFS= read -r -d '' arg; do actual+=("$arg"); done < "$TEST_DIR/build-$number"
  [[ ${#actual[@]} -eq ${#expected[@]} ]] || fail "build $number: argument count differs"
  for arg in "${expected[@]}"; do
    [[ "${actual[$i]}" == "$arg" ]] || fail "build $number: argument $i differs"
    i=$((i + 1))
  done
  grep -Fqx -- "running: docker ${expected[*]}" "$TEST_DIR/log" || fail 'build log differs from argv'
}
assert_images() {
  local setup="$1" revision="$2" count=4
  local -a revision_args=() variant=()
  if [[ -n "$revision" ]]; then
    revision_args=(--build-arg "REVISION=$revision" --label "org.opencontainers.image.revision=$revision")
  fi
  if [[ "$setup" == standalone ]]; then
    count=5
    variant=(--build-arg IMAGE_VARIANT=slim)
  fi
  [[ "$BUILDS" -eq "$count" ]] || fail "expected $count builds, got $BUILDS"
  assert_build 1 build -t futureagi/future-agi:local \
    ${revision_args[@]+"${revision_args[@]}"} -f futureagi/Dockerfile.oss \
    ${variant[@]+"${variant[@]}"} futureagi
  assert_build 2 build -t futureagi/frontend:local \
    ${revision_args[@]+"${revision_args[@]}"} frontend
  assert_build 3 build -t futureagi/fi-collector:local \
    ${revision_args[@]+"${revision_args[@]}"} fi-collector
  assert_build 4 build -t futureagi/agentcc-gateway:local \
    ${revision_args[@]+"${revision_args[@]}"} agentcc-gateway
  if [[ "$setup" == standalone ]]; then
    assert_build 5 build -t futureagi/standalone:local \
      ${revision_args[@]+"${revision_args[@]}"} -f deploy/standalone/Dockerfile \
      --build-arg BACKEND_IMAGE=futureagi/future-agi:local \
      --build-arg FRONTEND_IMAGE=futureagi/frontend:local \
      --build-arg FI_COLLECTOR_IMAGE=futureagi/fi-collector:local \
      --build-arg AGENTCC_GATEWAY_IMAGE=futureagi/agentcc-gateway:local deploy/standalone
  fi
}
explicit_revision() {
  export FUTURE_AGI_REVISION="$TEST_REVISION"
  # Called indirectly by the sourced build helper.
  # shellcheck disable=SC2329
  git() { fail 'explicit revision must not consult git'; }
  build_local_images "$1" all
  assert_images "$1" "$TEST_REVISION"
}
invalid_revision() {
  local result
  local -x FUTURE_AGI_REVISION="$1"
  if (build_local_images distributed all) 2> "$TEST_DIR/error"; then
    fail 'invalid revision was accepted'
  else
    result=$?
  fi
  [[ "$result" -eq 64 ]] || fail 'invalid revision did not call die'
  grep -q 'FUTURE_AGI_REVISION' "$TEST_DIR/error" || fail 'error does not name the setting'
  [[ ! -e "$TEST_DIR/build-1" ]] || fail 'docker was invoked before rejection'
}
checkout_revision() {
  if [[ "${2:-unset}" == empty ]]; then export FUTURE_AGI_REVISION=; fi
  build_local_images "$1" all
  assert_images "$1" "$HEAD_REVISION"
}
archive_revision() {
  cd "$TMP/archive"
  if [[ "${2:-unset}" == empty ]]; then export FUTURE_AGI_REVISION=; fi
  build_local_images "$1" all
  assert_images "$1" ''
}
explicit_archive_revision() {
  cd "$TMP/archive"
  explicit_revision standalone
}
missing_git() {
  # Called indirectly by the sourced build helper.
  # shellcheck disable=SC2329
  git() { return 127; }
  build_local_images standalone all
  assert_images standalone ''
}
resolve_once_per_image() {
  # Called indirectly by the sourced build helper.
  # shellcheck disable=SC2329
  git() { printf 'lookup\n' >> "$TEST_DIR/git-calls"; printf '%s\n' "$HEAD_REVISION"; }
  build_local_images standalone all
  assert_images standalone "$HEAD_REVISION"
  [[ "$(wc -l < "$TEST_DIR/git-calls" | tr -d ' ')" == 5 ]] || fail 'revision was not resolved once per build'
}
quoted_arguments() {
  export FUTURE_AGI_REVISION="$TEST_REVISION"
  build_local_image futureagi/test:local -f 'some Dockerfile' 'context with spaces'
  assert_build 1 build -t futureagi/test:local --build-arg "REVISION=$TEST_REVISION" \
    --label "org.opencontainers.image.revision=$TEST_REVISION" -f 'some Dockerfile' 'context with spaces'
}
code_executor_image() {
  export FUTURE_AGI_REVISION="$TEST_REVISION"
  build_local_code_executor
  [[ "$BUILDS" -eq 1 ]] || fail "expected 1 build, got $BUILDS"
  assert_build 1 build -t futureagi/code-executor:local --build-arg "REVISION=$TEST_REVISION" \
    --label "org.opencontainers.image.revision=$TEST_REVISION" futureagi/code-executor
}
code_executor_base_unpublished() {
  # A base the Dockerfile pins that is neither on this host nor published is
  # built first, under that tag, from Dockerfile.base.
  local base
  base="$(sed -n 's/^ARG CODE_EXECUTOR_BASE=//p' "$ROOT/futureagi/code-executor/Dockerfile" | head -n 1)"
  [[ -n "$base" ]] || fail 'the executor Dockerfile pins no CODE_EXECUTOR_BASE'
  export FUTURE_AGI_REVISION="$TEST_REVISION" DOCKER_LOOKUP=1
  build_local_code_executor
  [[ "$BUILDS" -eq 2 ]] || fail "expected 2 builds, got $BUILDS"
  assert_build 1 build -t "$base" --build-arg "REVISION=$TEST_REVISION" \
    --label "org.opencontainers.image.revision=$TEST_REVISION" \
    -f futureagi/code-executor/Dockerfile.base futureagi/code-executor
  assert_build 2 build -t futureagi/code-executor:local --build-arg "REVISION=$TEST_REVISION" \
    --label "org.opencontainers.image.revision=$TEST_REVISION" futureagi/code-executor
}
code_executor_base_published() {
  # Not on this host but published: Docker pulls it for the build; nothing is built first.
  export FUTURE_AGI_REVISION="$TEST_REVISION" DOCKER_IMAGE_LOOKUP=1 DOCKER_MANIFEST_LOOKUP=0
  build_local_code_executor
  [[ "$BUILDS" -eq 1 ]] || fail "expected 1 build, got $BUILDS"
  assert_build 1 build -t futureagi/code-executor:local --build-arg "REVISION=$TEST_REVISION" \
    --label "org.opencontainers.image.revision=$TEST_REVISION" futureagi/code-executor
}
code_executor_dockerfile_missing() {
  local result
  if (cd "$TMP/archive" && build_local_code_executor) 2> "$TEST_DIR/error"; then
    fail 'a missing executor Dockerfile was accepted'
  else
    result=$?
  fi
  [[ "$result" -eq 64 ]] || fail 'a missing executor Dockerfile did not call die'
  grep -q 'futureagi/code-executor/Dockerfile' "$TEST_DIR/error" || fail 'error does not name the Dockerfile'
}
install_builds_the_code_executor_from_source() {
  # The stack runs the sandbox as a non-root user on a read-only filesystem; a
  # published executor of another release cannot start under those settings,
  # so --from-source must build it and point the stack at that image.
  local block
  block="$(awk '/^if \(\( FROM_SOURCE == 1 \)\); then$/{p=1} p{print} p&&/^fi$/{exit}' "$ROOT/bin/install")"
  [[ -n "$block" ]] || fail 'bin/install has no --from-source block'
  grep -Fq 'build_local_code_executor' <<<"$block" || fail '--from-source does not build the code executor'
  grep -Fq 'set_env_var CODE_EXECUTOR_VERSION local' <<<"$block" ||
    fail '--from-source does not point CODE_EXECUTOR_VERSION at the local image'
}

passed=0 failed=0
run_test() {
  local name="$1" result
  shift
  set +e
  (
    set -e
    TEST_DIR="$TMP/$name"
    mkdir "$TEST_DIR"
    BUILDS=0
    cd "$ROOT"
    "$@"
  )
  result=$?
  set -e
  if [[ "$result" -eq 0 ]]; then
    printf 'PASS %s\n' "$name"
    passed=$((passed + 1))
  else
    printf 'FAIL %s\n' "$name"
    failed=$((failed + 1))
  fi
}

run_test explicit-distributed explicit_revision distributed
run_test explicit-standalone explicit_revision standalone
run_test invalid-unknown invalid_revision unknown
run_test invalid-short invalid_revision abcdef0
run_test invalid-uppercase invalid_revision AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
run_test invalid-whitespace invalid_revision " $TEST_REVISION"
run_test checkout-distributed checkout_revision distributed
run_test checkout-standalone checkout_revision standalone
run_test empty-checkout checkout_revision standalone empty
run_test archive-distributed archive_revision distributed
run_test archive-standalone archive_revision standalone
run_test empty-archive archive_revision standalone empty
run_test explicit-archive explicit_archive_revision
run_test missing-git missing_git
run_test resolve-once-per-image resolve_once_per_image
run_test quoted-arguments quoted_arguments
run_test code-executor-image code_executor_image
run_test code-executor-base-unpublished code_executor_base_unpublished
run_test code-executor-base-published code_executor_base_published
run_test code-executor-dockerfile-missing code_executor_dockerfile_missing
run_test install-builds-code-executor install_builds_the_code_executor_from_source
printf 'RESULT passed=%s failed=%s\n' "$passed" "$failed"
[[ "$failed" -eq 0 ]]
