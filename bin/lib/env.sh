# shellcheck shell=bash
# .env access for bin/install, bin/uninstall and bin/dev. Sourced, with the
# repository root as the working directory.

# The value of a .env line, from the text after its "=", as Compose reads it:
# leading whitespace goes; a value in matching double or single quotes is what
# lies between them, when only whitespace or a # comment follows the closing
# quote; an unquoted value ends before " #" (a space, then #) and loses
# trailing whitespace. Text Compose reads no other way, an unclosed quote, is
# kept as written. Escapes and ${VAR} in values are left as they are.
env_parse_value() { # TEXT
  local text="$1" value quote inner rest comment=' #'
  value="${text#"${text%%[![:space:]]*}"}"
  quote="${value:0:1}"
  if [[ "$quote" == '"' || "$quote" == "'" ]]; then
    inner="${value:1}"
    if [[ "$inner" == *"$quote"* ]]; then
      rest="${inner#*"$quote"}"
      rest="${rest#"${rest%%[![:space:]]*}"}"
      if [[ -z "$rest" || "$rest" == '#'* ]]; then
        printf '%s' "${inner%%"$quote"*}"
        return 0
      fi
    fi
    printf '%s' "$text"
    return 0
  fi
  value="${value%%"$comment"*}"
  printf '%s' "${value%"${value##*[![:space:]]}"}"
}

# The text after the "=" of VAR's last line in .env, as written, or nothing.
# Copy this, not env_value, from one key to another: Compose then reads the
# same value from both lines, quotes, " #" and surrounding spaces included.
env_text() { # VAR
  if [[ -f .env ]]; then
    grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true
  fi
}

# Value of VAR in .env (its last line) as Compose reads it, or DEFAULT when
# that is unset or empty.
env_value() { # VAR [DEFAULT]
  local value
  value=$(env_parse_value "$(env_text "$1")")
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
    # Existing values (e.g. retired peppers) are literal, not sed replacements.
    val=${val//\\/\\\\}
    val=${val//&/\\&}
    val=${val//|/\\|}
    sed_inplace -E "s|^${var}=.*|${var}=${val}|" .env
  else
    printf "%s=%s\n" "$var" "$val" >> .env
  fi
}
