# shellcheck shell=bash
# Host conditions under which the code-evaluation sandbox (the code-executor's
# nsjail) cannot build a run's jail: each run needs Linux user namespaces inside
# its container. Prints one line per condition found, and nothing when none
# applies or the host cannot be read. Sourced by bin/install after env.sh;
# reads only.

# The AppArmor profile Compose runs the code-executor under: the shell, then
# .env, then the compose files' default (unconfined).
sandbox_apparmor_profile() {
  local value
  if declare -F compose_env_value > /dev/null; then
    value="$(compose_env_value CODE_EXECUTOR_APPARMOR_PROFILE unconfined)"
  else
    value="${CODE_EXECUTOR_APPARMOR_PROFILE:-unconfined}"
  fi
  printf '%s' "${value:-unconfined}"
}

# Whether the AppArmor profile NAME is loaded: 0 yes, 1 no, 2 cannot tell. The
# kernel lists loaded profiles for any user as directories with a name file;
# its profiles file needs root.
sandbox_apparmor_loaded() { # NAME
  local dir="${SANDBOX_APPARMOR_POLICY:-/sys/kernel/security/apparmor/policy/profiles}" entry
  [[ -d "$dir" && -r "$dir" && -x "$dir" ]] || return 2
  for entry in "$dir"/*/name; do
    if [[ -r "$entry" && "$(cat -- "$entry" 2>/dev/null)" == "$1" ]]; then
      return 0
    fi
  done
  return 1
}

# The configured AppArmor profile when this Linux host visibly has not loaded
# it: Docker then cannot start the code-executor. Nothing otherwise.
sandbox_unloaded_profile() {
  local profile loaded=0
  [[ "$(uname -s 2>/dev/null || true)" == Linux ]] || return 0
  profile="$(sandbox_apparmor_profile)"
  [[ "$profile" != unconfined ]] || return 0
  sandbox_apparmor_loaded "$profile" || loaded=$?
  if (( loaded == 1 )); then
    printf '%s\n' "$profile"
  fi
  return 0
}

sandbox_host_conditions() {
  local value security loaded=0
  if [[ "$(uname -s 2>/dev/null || true)" == Linux ]]; then
    value="$(sysctl -n kernel.apparmor_restrict_unprivileged_userns 2>/dev/null || true)"
    if [[ "$value" == 1 ]]; then
      # Only the shipped profile (deploy/apparmor/fa-code-executor), seen
      # loaded, lifts the restriction for the executor.
      if [[ "$(sandbox_apparmor_profile)" == fa-code-executor ]]; then
        sandbox_apparmor_loaded fa-code-executor || loaded=$?
      else
        loaded=1
      fi
      if (( loaded != 0 )); then
        echo "the host restricts unprivileged user namespaces (kernel.apparmor_restrict_unprivileged_userns=1, the default on Ubuntu 23.10 and later), and the code executor is not set to run under the loaded fa-code-executor AppArmor profile (CODE_EXECUTOR_APPARMOR_PROFILE)"
      fi
    fi
  fi
  security="$(docker info --format '{{json .SecurityOptions}}' 2>/dev/null || true)"
  if [[ "$security" == *'name=userns'* ]]; then
    echo "Docker runs with userns-remap, so every container is already inside a remapped user namespace"
  fi
  return 0
}
