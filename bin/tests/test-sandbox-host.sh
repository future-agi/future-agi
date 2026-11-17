#!/usr/bin/env bash
# Run with bash bin/tests/test-sandbox-host.sh. No Docker daemon or dependencies.
# bin/lib/sandbox-host.sh names the host conditions under which the code-evaluation
# sandbox cannot build a run's jail; bin/install warns about them before installing.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
fail() { printf '  %s\n' "$*" >&2; exit 1; }
UNAME=Linux SYSCTL_VALUE=0 DOCKER_SECURITY='["name=apparmor","name=seccomp,profile=builtin","name=cgroupns"]'
unset CODE_EXECUTOR_APPARMOR_PROFILE
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
# bin/install sources env.sh before sandbox-host.sh and reads .env from the
# repository root; the tests read a .env of their own in an empty directory.
# shellcheck source=../lib/env.sh
source "$ROOT/bin/lib/env.sh"
# shellcheck source=../lib/sandbox-host.sh
source "$ROOT/bin/lib/sandbox-host.sh"
WORK="$(mktemp -d)"
trap 'cd /; rm -rf "$WORK"' EXIT
cd "$WORK"
# The loaded AppArmor profiles, as the kernel lists them for any user: one
# directory per profile with a name file. Empty here: nothing is loaded.
SANDBOX_APPARMOR_POLICY="$WORK/policy"
loaded_profile() { mkdir -p "$SANDBOX_APPARMOR_POLICY/$1.3"; printf '%s\n' "$1" > "$SANDBOX_APPARMOR_POLICY/$1.3/name"; }

stock_host_has_none() {
  [[ -z "$(sandbox_host_conditions)" ]] || fail 'a stock host reported a condition'
}
restricted_user_namespaces() {
  SYSCTL_VALUE=1
  local out; out="$(sandbox_host_conditions)"
  [[ "$(printf '%s\n' "$out" | wc -l | tr -d ' ')" == 1 ]] || fail "expected one condition: $out"
  grep -Fq 'kernel.apparmor_restrict_unprivileged_userns=1' <<<"$out" || fail 'does not name the setting'
  grep -Fq 'fa-code-executor' <<<"$out" || fail "does not name the AppArmor profile that lifts it: $out"
}
restricted_with_the_profile_configured_is_silent() {
  SYSCTL_VALUE=1
  loaded_profile fa-code-executor
  printf 'CODE_EXECUTOR_APPARMOR_PROFILE=fa-code-executor\n' > .env
  [[ -z "$(sandbox_host_conditions)" ]] || fail 'reported the restriction although .env runs the executor under the profile'
  rm -f .env
  CODE_EXECUTOR_APPARMOR_PROFILE=fa-code-executor
  export CODE_EXECUTOR_APPARMOR_PROFILE
  [[ -z "$(sandbox_host_conditions)" ]] || fail 'ignored the profile exported in the shell, which Compose reads first'
}
restricted_with_an_unconfined_executor_reports() {
  SYSCTL_VALUE=1
  printf 'CODE_EXECUTOR_APPARMOR_PROFILE=unconfined\n' > .env
  grep -Fq 'kernel.apparmor_restrict_unprivileged_userns=1' <<<"$(sandbox_host_conditions)" || fail 'unconfined hid the restriction'
  printf 'CODE_EXECUTOR_APPARMOR_PROFILE=\n' > .env
  grep -Fq 'kernel.apparmor_restrict_unprivileged_userns=1' <<<"$(sandbox_host_conditions)" || fail 'an empty value hid the restriction'
}
the_profile_does_not_hide_userns_remap() {
  SYSCTL_VALUE=1 DOCKER_SECURITY='["name=userns"]'
  loaded_profile fa-code-executor
  printf 'CODE_EXECUTOR_APPARMOR_PROFILE=fa-code-executor\n' > .env
  local out; out="$(sandbox_host_conditions)"
  [[ "$(printf '%s\n' "$out" | wc -l | tr -d ' ')" == 1 ]] || fail "expected only userns-remap: $out"
  grep -Fq 'userns-remap' <<<"$out" || fail "the profile hid userns-remap: $out"
}
restricted_with_the_profile_not_loaded_still_reports() {
  SYSCTL_VALUE=1
  printf 'CODE_EXECUTOR_APPARMOR_PROFILE=fa-code-executor\n' > .env
  grep -Fq 'kernel.apparmor_restrict_unprivileged_userns=1' <<<"$(sandbox_host_conditions)" || fail 'went quiet for a profile that is not loaded'
}
only_the_shipped_profile_name_is_trusted() {
  SYSCTL_VALUE=1
  loaded_profile site-executor
  printf 'CODE_EXECUTOR_APPARMOR_PROFILE=site-executor\n' > .env
  grep -Fq 'kernel.apparmor_restrict_unprivileged_userns=1' <<<"$(sandbox_host_conditions)" || fail 'went quiet for a profile other than fa-code-executor'
  [[ -z "$(sandbox_unloaded_profile)" ]] || fail 'named a loaded profile as not loaded'
}
an_unloaded_profile_is_named_on_any_linux_host() {
  printf 'CODE_EXECUTOR_APPARMOR_PROFILE=fa-code-executor\n' > .env
  [[ "$(sandbox_unloaded_profile)" == fa-code-executor ]] || fail 'did not name the profile that is not loaded'
  [[ -z "$(sandbox_host_conditions)" ]] || fail 'reported a sandbox condition on an unrestricted host'
  loaded_profile fa-code-executor
  [[ -z "$(sandbox_unloaded_profile)" ]] || fail 'named a loaded profile'
  printf 'CODE_EXECUTOR_APPARMOR_PROFILE=unconfined\n' > .env
  rm -rf "$SANDBOX_APPARMOR_POLICY"/*
  [[ -z "$(sandbox_unloaded_profile)" ]] || fail 'named unconfined, which is no profile'
}
cannot_tell_is_not_called_unloaded() {
  printf 'CODE_EXECUTOR_APPARMOR_PROFILE=fa-code-executor\n' > .env
  rm -rf "$SANDBOX_APPARMOR_POLICY"
  [[ -z "$(sandbox_unloaded_profile)" ]] || fail 'called a profile not loaded on a host that does not list its profiles'
  SYSCTL_VALUE=1
  grep -Fq 'kernel.apparmor_restrict_unprivileged_userns=1' <<<"$(sandbox_host_conditions)" || fail 'went quiet without seeing the profile loaded'
  UNAME=Darwin
  [[ -z "$(sandbox_unloaded_profile)" ]] || fail 'checked AppArmor on a non-Linux host'
}
install_stops_before_pulling_when_the_profile_is_not_loaded() {
  local check pull message
  check="$(grep -n '^sandbox_unloaded="$(sandbox_unloaded_profile)"$' "$ROOT/bin/install" | cut -d: -f1)"
  pull="$(grep -n 'docker compose pull failed' "$ROOT/bin/install" | head -n 1 | cut -d: -f1)"
  [[ -n "$check" && -n "$pull" && "$check" -lt "$pull" ]] || fail 'bin/install does not check the configured profile before it pulls'
  message="$(sed -n "${check},\$p" "$ROOT/bin/install" | awk '/^fi$/{exit} {print}')"
  grep -Fq 'preflight_fail' <<<"$message" || fail 'a profile that is not loaded does not stop the install'
  grep -Fq 'INSTALLATION.md' <<<"$message" || fail 'the stop does not point to INSTALLATION.md'
  ! grep -Eq 'sudo|apparmor_parser|sysctl -w' <<<"$message" || fail 'the stop offers a host command'
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
  ! grep -Eq 'sysctl -w|sudo|apparmor_parser' <<<"$block" || fail 'the installer changes or offers to change the host'
  ! grep -Eq 'sysctl -w|sudo|apparmor_parser' "$ROOT/bin/lib/sandbox-host.sh" || fail 'the helper changes or offers to change the host'
}
the_profile_ships_with_only_the_userns_rule() {
  local profile="$ROOT/deploy/apparmor/fa-code-executor" rules
  [[ -f "$profile" ]] || fail 'deploy/apparmor/fa-code-executor is missing'
  grep -Eq '^profile fa-code-executor flags=\(unconfined\) \{$' "$profile" || fail 'the profile is not the unconfined fa-code-executor profile'
  rules="$(sed -e 's/#.*//' -e '/^[[:space:]]*$/d' "$profile")"
  grep -Eq '^[[:space:]]+userns,$' <<<"$rules" || fail 'the profile has no userns rule'
  [[ "$(grep -Ec '^[[:space:]]+[a-z].*,$' <<<"$rules")" == 1 ]] || fail "the profile grants more than userns: $rules"
  if command -v apparmor_parser > /dev/null 2>&1; then
    # Parse only: -Q skips the kernel, -K the cache, so this changes nothing on the host.
    apparmor_parser -Q -K "$profile" > /dev/null || fail 'apparmor_parser does not accept the profile'
  fi
}
compose_runs_the_executor_under_the_setting() {
  local file
  for file in docker-compose.yml docker-compose.distributed.yml; do
    grep -Fq -- '- apparmor:${CODE_EXECUTOR_APPARMOR_PROFILE:-unconfined}' "$ROOT/$file" || fail "$file does not read CODE_EXECUTOR_APPARMOR_PROFILE"
    ! grep -Fq -- '- apparmor:unconfined' "$ROOT/$file" || fail "$file still fixes the executor at apparmor:unconfined"
  done
}

passed=0 failed=0
run_test() {
  local name="$1"; shift
  rm -f "$WORK/.env"
  rm -rf "$SANDBOX_APPARMOR_POLICY"; mkdir -p "$SANDBOX_APPARMOR_POLICY"
  if ( set -e; "$@" ); then printf 'PASS %s\n' "$name"; passed=$((passed + 1)); else printf 'FAIL %s\n' "$name"; failed=$((failed + 1)); fi
}
run_test stock-host-has-none stock_host_has_none
run_test restricted-user-namespaces restricted_user_namespaces
run_test restricted-with-the-profile-configured-is-silent restricted_with_the_profile_configured_is_silent
run_test restricted-with-an-unconfined-executor-reports restricted_with_an_unconfined_executor_reports
run_test the-profile-does-not-hide-userns-remap the_profile_does_not_hide_userns_remap
run_test restricted-with-the-profile-not-loaded-still-reports restricted_with_the_profile_not_loaded_still_reports
run_test only-the-shipped-profile-name-is-trusted only_the_shipped_profile_name_is_trusted
run_test an-unloaded-profile-is-named-on-any-linux-host an_unloaded_profile_is_named_on_any_linux_host
run_test cannot-tell-is-not-called-unloaded cannot_tell_is_not_called_unloaded
run_test install-stops-before-pulling-when-the-profile-is-not-loaded install_stops_before_pulling_when_the_profile_is_not_loaded
run_test userns-remap userns_remap
run_test both-conditions both
run_test not-linux-or-unknown-is-silent not_linux_or_unknown_is_silent
run_test install-warns-and-continues install_warns_and_continues
run_test the-profile-ships-with-only-the-userns-rule the_profile_ships_with_only_the_userns_rule
run_test compose-runs-the-executor-under-the-setting compose_runs_the_executor_under_the_setting
printf 'RESULT passed=%s failed=%s\n' "$passed" "$failed"
[[ "$failed" -eq 0 ]]
