# shellcheck shell=bash
# .env access for bin/install, bin/uninstall and bin/dev. Sourced, with the
# repository root as the working directory.

# Value of VAR in .env (its last line), or DEFAULT when that is unset or empty.
env_value() { # VAR [DEFAULT]
  local value=""
  if [[ -f .env ]]; then
    value=$(grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true)
  fi
  printf '%s' "${value:-${2:-}}"
}

# VAR as Compose resolves it: exported in the shell, else from .env, else
# DEFAULT.
compose_env_value() { # VAR [DEFAULT]
  local value="${!1:-}"
  printf '%s' "${value:-$(env_value "$1" "${2:-}")}"
}

# The Compose project: the environment, then .env, then the compose files'
# `name:`. bin/install --new-instance writes futureagi-2, -3, ... to .env.
compose_project_name() {
  compose_env_value COMPOSE_PROJECT_NAME futureagi
}

# Portable in-place sed (BSD on mac, GNU on linux).
sed_inplace() {
  if [[ "$(uname -s)" == "Darwin" ]]; then
    sed -i '' "$@"
  else
    sed -i "$@"
  fi
}

# Set VAR=VALUE in .env (replace if present, append otherwise).
set_env_var() { # VAR VALUE
  local var="$1" val="$2"
  if grep -Eq "^${var}=" .env; then
    sed_inplace -E "s|^${var}=.*|${var}=${val}|" .env
  else
    printf "%s=%s\n" "$var" "$val" >> .env
  fi
}
