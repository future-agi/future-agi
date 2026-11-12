#!/usr/bin/env bash
# Run with bash bin/tests/test-sandbox-host.sh. No Docker daemon or dependencies.
# bin/lib/sandbox-host.sh names the host conditions under which the code-evaluation
# sandbox cannot build a run's jail; bin/install warns about them before installing.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
fail() { printf '  %s\n' "$*" >&2; exit 1; }
UNAME=Linux SYSCTL_VALUE=0 DOCKER_SECURITY='["name=apparmor","name=seccomp,profile=builtin","name=cgroupns"]'
uname() { printf '%s\n' "$UNAME"; }
sysctl() {
  [[ "$*" == "-n kernel.apparmor_restrict_unprivileged_userns" ]] || return 1
  [[ "$SYSCTL_VALUE" == missing ]] && return 255
  printf '%s\n' "$SYSCTL_VALUE"
}
docker() {
  [[ "$1 $2" == "info --format" ]] || return 1
  printf '%s\n' "$DOCKER_SECURITY"
}
# shellcheck source=../lib/sandbox-host.sh
source "$ROOT/bin/lib/sandbox-host.sh"

stock_host_has_none() {
  [[ -z "$(sandbox_host_conditions)" ]] || fail 'a stock host reported a condition'
}
restricted_user_namespaces() {
  SYSCTL_VALUE=1
  local out; out="$(sandbox_host_conditions)"
  [[ "$(printf '%s\n' "$out" | wc -l | tr -d ' ')" == 1 ]] || fail "expected one condition: $out"
  grep -Fq 'kernel.apparmor_restrict_unprivileged_userns=1' <<<"$out" || fail 'does not name the setting'
}
userns_remap() {
  DOCKER_SECURITY='["name=apparmor","name=seccomp,profile=builtin","name=userns","name=cgroupns"]'
  local out; out="$(sandbox_host_conditions)"
  grep -Fq 'userns-remap' <<<"$out" || fail "does not name userns-remap: $out"
  ! grep -Fq 'apparmor_restrict' <<<"$out" || fail 'named a setting that is 0'
}
both() {
  SYSCTL_VALUE=1 DOCKER_SECURITY='["name=userns"]'
  [[ "$(sandbox_host_conditions | wc -l | tr -d ' ')" == 2 ]] || fail 'expected both conditions'
}
not_linux_or_unknown_is_silent() {
  UNAME=Darwin SYSCTL_VALUE=1
  [[ -z "$(sandbox_host_conditions)" ]] || fail 'a non-Linux host reported the kernel setting'
  UNAME=Linux SYSCTL_VALUE=missing DOCKER_SECURITY=''
  [[ -z "$(sandbox_host_conditions)" ]] || fail 'an unreadable setting or Docker reported a condition'
}
install_warns_and_continues() {
  # The installer sources the helper, warns before anything is installed, and does not stop.
  grep -Fq '. "$ROOT/bin/lib/sandbox-host.sh"' "$ROOT/bin/install" || fail 'bin/install does not source sandbox-host.sh'
  local block
  block="$(awk '/^sandbox_conditions="\$\(sandbox_host_conditions\)"$/{p=1} p{print} p&&/^fi$/{exit}' "$ROOT/bin/install")"
  [[ -n "$block" ]] || fail 'bin/install does not check the sandbox host conditions'
  grep -Fq 'Code evaluations will not run on this host as configured' <<<"$block" || fail 'the warning does not say code evaluations will not run'
  grep -Fq 'warn ' <<<"$block" || fail 'the check does not warn'
  ! grep -Eq 'preflight_fail|die |exit ' <<<"$block" || fail 'the check stops the install'
  ! grep -Eq 'sysctl -w|sudo' <<<"$block" || fail 'the installer changes or offers to change the host'
}

passed=0 failed=0
run_test() {
  local name="$1"; shift
  if ( set -e; "$@" ); then printf 'PASS %s\n' "$name"; passed=$((passed + 1)); else printf 'FAIL %s\n' "$name"; failed=$((failed + 1)); fi
}
run_test stock-host-has-none stock_host_has_none
run_test restricted-user-namespaces restricted_user_namespaces
run_test userns-remap userns_remap
run_test both-conditions both
run_test not-linux-or-unknown-is-silent not_linux_or_unknown_is_silent
run_test install-warns-and-continues install_warns_and_continues
printf 'RESULT passed=%s failed=%s\n' "$passed" "$failed"
[[ "$failed" -eq 0 ]]
