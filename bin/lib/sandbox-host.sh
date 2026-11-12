# shellcheck shell=bash
# Host conditions under which the code-evaluation sandbox (the code-executor's
# nsjail) cannot build a run's jail: each run needs Linux user namespaces inside
# its container. Prints one line per condition found, and nothing when none
# applies or the host cannot be read. Sourced by bin/install; reads only.
sandbox_host_conditions() {
  local value security
  if [[ "$(uname -s 2>/dev/null || true)" == Linux ]]; then
    value="$(sysctl -n kernel.apparmor_restrict_unprivileged_userns 2>/dev/null || true)"
    if [[ "$value" == 1 ]]; then
      echo "the host restricts unprivileged user namespaces (kernel.apparmor_restrict_unprivileged_userns=1, the default on Ubuntu 23.10 and later)"
    fi
  fi
  security="$(docker info --format '{{json .SecurityOptions}}' 2>/dev/null || true)"
  if [[ "$security" == *'name=userns'* ]]; then
    echo "Docker runs with userns-remap, so every container is already inside a remapped user namespace"
  fi
  return 0
}
