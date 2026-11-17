#!/usr/bin/env bash
# Interactive production setup for self-hosted Future AGI.
# Preserves existing configuration; new files require supplied catalog credentials.
# Boot checks previously initialized schema/mirrors; it never initializes them.
#
# Usage: ./deploy/setup.sh [--skip-up] [--non-interactive] [--confirm-initialized]
# Optional status: --host-status-help or --prepare-host-status (alone; never boots).

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." &>/dev/null && pwd)"
# Deliberately separate from production setup/initialization and before all
# configuration writes or Docker calls. Existing installs remain untouched.
if [[ "${1:-}" == --host-status-help || "${1:-}" == --prepare-host-status ]]; then
  exec bash "${REPO_ROOT}/bin/install" "$@"
fi
ENV_FILE="${SCRIPT_DIR}/.env.production"
SECRET_NAMES=(SECRET_KEY FI_API_SECRET_PEPPER AGENTCC_INTERNAL_API_KEY AGENTCC_ADMIN_TOKEN AGENTCC_WEBHOOK_SECRET OMEGA_HUB_CHECKER_TOKEN PG_PASSWORD
  MINIO_ROOT_PASSWORD PROPERTY_CATALOG_API_PASSWORD
  PROPERTY_CATALOG_CONSUMER_PASSWORD OPENAI_API_KEY ANTHROPIC_API_KEY GOOGLE_API_KEY
  INTEGRATION_ENCRYPTION_KEY EE_LICENSE_KEY RECAPTCHA_SECRET_KEY MAILGUN_API_KEY
  AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY FUTURE_AGI_CLOUD_API_KEY
  GOOGLE_APPLICATION_CREDENTIALS)

SKIP_UP=0
NON_INTERACTIVE=0
CONFIRM_INITIALIZED=0
for arg in "$@"; do
  case "$arg" in
    --skip-up) SKIP_UP=1 ;;
    --non-interactive) NON_INTERACTIVE=1 ;;
    --confirm-initialized) CONFIRM_INITIALIZED=1 ;;
    -h|--help)
      sed -n '2,7p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'
YELLOW=$'\033[33m'; RESET=$'\033[0m'
say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$GREEN" "$RESET" "$*"; }
warn() { printf '%s⚠%s %s\n' "$YELLOW" "$RESET" "$*"; }
err()  { printf '%s✗%s %s\n' "$RED" "$RESET" "$*" >&2; }

require() {
  command -v "$1" >/dev/null 2>&1 || { err "missing dependency: $1"; exit 1; }
}

require docker
require openssl
docker compose version >/dev/null 2>&1 || { err "docker compose v2 required"; exit 1; }

prompt() {
  # prompt "Question" "default" → echoes user input or default.
  local question="$1" default="${2:-}" secret="${3:-0}" answer terminal_state
  if (( NON_INTERACTIVE )); then
    printf '%s\n' "$default"
    return
  fi
  if (( secret )); then
    # Disable echo BEFORE publishing the prompt (read -s -p has a prompt race).
    # Callers capture this function in a subshell; restore on success/EOF/signal.
    if [[ -t 0 ]]; then
      terminal_state=$(stty -g) || return 1
      # Capture stty's encoded state now; function locals expire before EXIT.
      trap "stty '$terminal_state' 2>/dev/null || true" EXIT
      stty -echo || return 1
    fi
    read -r -s -p "${question}: " answer || return 1
    if [[ -n "${terminal_state:-}" ]]; then
      stty "$terminal_state" || return 1
      trap - EXIT
    fi
    printf '\n' >&2
  elif [[ -n "$default" ]]; then
    read -r -p "${BOLD}${question}${RESET} ${DIM}[${default}]${RESET}: " answer || return 1
  else
    read -r -p "${BOLD}${question}${RESET}: " answer || return 1
  fi
  printf '%s\n' "${answer:-$default}"
}

prompt_required() {
  local name="$1" question="$2" secret="${3:-0}" answer
  answer="${!name:-}"
  if [[ -z "$answer" ]] && (( ! NON_INTERACTIVE )); then
    if (( secret )); then
      answer=$(prompt "$question" "" 1) || { err "$name is required (input closed)"; return 1; }
    else
      answer=$(prompt "$question" "") || { err "$name is required (input closed)"; return 1; }
    fi
  fi
  if [[ -z "$answer" || "$answer" == *$'\n'* || "$answer" == *$'\r'* ]]; then
    err "$name requires an explicit single-line value; set it or edit deploy/.env.production"
    return 1
  fi
  printf '%s\n' "$answer"
}

# Double-quoted dotenv values escape backslashes/quotes and disable interpolation.
# Single quoting alone is ambiguous for trailing backslashes and \\' sequences.
dotenv_literal() {
  printf '"'
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\$/$$/g'
  printf '"'
}

# The text after the "=" of NAME's last assignment, trailing spaces trimmed, or
# nothing. Callers only compare or decode it; it is never printed.
setting_text() { # NAME
  local raw
  raw=$(sed -nE "s/^[[:space:]]*(export[[:space:]]+)?$1[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" | tail -n 1)
  printf '%s' "${raw%"${raw##*[![:space:]]}"}"
}

# True when Compose would read no NAME from the file: no assignment, or a last
# one that is empty. The value is never printed.
setting_missing() { # NAME
  local raw
  raw=$(setting_text "$1")
  [[ -z "$raw" || "$raw" == '""' || "$raw" == "''" ]]
}

# NAME's value as Compose reads it, when that takes no guessing: unquoted
# without "$", in single quotes without a backslash, or in double quotes
# without a backslash or "$" (a comment after the closing quote is dropped).
# Fails for anything else. The value is never printed.
setting_literal() { # NAME
  local raw value
  local single="^'([^'\\\\]*)'[[:space:]]*(#.*)?$" double='^"([^"\\$]*)"[[:space:]]*(#.*)?$'
  raw=$(setting_text "$1")
  if [[ "$raw" =~ $single || "$raw" =~ $double ]]; then
    value=${BASH_REMATCH[1]}
  elif [[ "$raw" != [\"\']* && "$raw" != *'$'* ]]; then
    value=${raw%%[[:space:]]#*}
    value="${value%"${value##*[![:space:]]}"}"
  else
    return 1
  fi
  printf '%s' "$value"
}

PUBLISHED_PEPPER=local-dev-only-api-secret-pepper-replace-me

# True when the API secret pepper rule replaces VALUE: empty, the published
# default or a CHANGEME- placeholder (bin/lib/secrets.sh).
pepper_replaced() { # VALUE
  [[ -z "$1" || "$1" == "$PUBLISHED_PEPPER" || "$1" == CHANGEME-* ]]
}

# RETIRED with VALUE added, as bin/lib/secrets.sh builds it: each entry
# trimmed and kept once, in first-occurrence order. Fails on an entry that
# cannot be kept as written: a single quote, two backslashes or a trailing
# backslash. Nothing is printed but the list itself.
retired_with() { # RETIRED VALUE
  local part list=""
  local -a entries=()
  IFS=, read -r -a entries <<< "$1,$2"
  for part in "${entries[@]}"; do
    part="${part#"${part%%[![:space:]]*}"}"
    part="${part%"${part##*[![:space:]]}"}"
    [[ -n "$part" && ",$list," != *,"$part",* ]] || continue
    if [[ "$part" == *\'* || "$part" == *\\\\* || "$part" == *\\ ]]; then return 1; fi
    list+="${list:+,}$part"
  done
  printf '%s' "$list"
}

# NAME=VALUE on a line of its own at the end of the file; Compose reads a
# name's last line.
append_setting() { # NAME VALUE
  if [[ -s "$ENV_FILE" && -n "$(tail -c 1 "$ENV_FILE")" ]]; then printf '\n' >> "$ENV_FILE"; fi
  { printf '%s=' "$1"; dotenv_literal "$2"; printf '\n'; } >> "$ENV_FILE"
}

# The COMPOSE_PROFILES Compose reads for the commands below: an exported value
# wins over the env file, where the name's last line counts.
compose_profiles() {
  local raw pattern='^[[:space:]]*(export[[:space:]]+)?COMPOSE_PROFILES[[:space:]]*=[[:space:]]*'
  if [[ -n "${COMPOSE_PROFILES+set}" ]]; then
    printf '%s' "$COMPOSE_PROFILES"
    return
  fi
  raw=$(sed -nE "s/${pattern}//p" "$ENV_FILE" | tail -n 1)
  raw="${raw%"${raw##*[![:space:]]}"}"
  case "$raw" in
    \"*) raw="${raw#\"}"; raw="${raw%%\"*}" ;;
    \'*) raw="${raw#\'}"; raw="${raw%%\'*}" ;;
    *) raw="${raw%%[[:space:]]#*}" ;;
  esac
  printf '%s' "$raw"
}

# True when COMPOSE_PROFILES lists `hub` or `*` (each entry stripped of spaces):
# the Omega Hub then runs, and its production settings and services come from
# deploy/docker-compose.production.hub.yml.
hub_profile_enabled() {
  local profile rest
  rest="$(compose_profiles),"
  while [[ -n "$rest" ]]; do
    profile="${rest%%,*}"
    rest="${rest#*,}"
    profile="${profile#"${profile%%[![:space:]]*}"}"
    profile="${profile%"${profile##*[![:space:]]}"}"
    if [[ "$profile" == hub || "$profile" == '*' ]]; then
      return 0
    fi
  done
  return 1
}

if [[ -f "$ENV_FILE" ]]; then
  # Stored credentials are never encrypted under a key made up for one process,
  # and the gateway's request logs are refused without the secret it shares
  # with the backend, so either one that is missing is added once, as for a
  # new file: the exported value, else a new one. Filling rotates nothing; a
  # value the file sets is never changed, except an API secret pepper anyone
  # can read (below).
  added=0
  # FI_API_SECRET_PEPPER follows bin/install's rule (bin/lib/secrets.sh
  # rotate_published_api_secret_pepper), which bin/update applies too: a
  # missing, empty, published-default or CHANGEME- pepper is replaced, and the
  # value Compose used until now (the published default when there was none)
  # goes to FI_API_SECRET_PEPPERS_RETIRED first, so API keys sealed under it
  # still verify. A missing one takes the exported value, as above, unless
  # that is a default too. Any other pepper is the operator's and is kept.
  # Lines are added at the end, as above: Compose reads a name's last line.
  if pepper=$(setting_literal FI_API_SECRET_PEPPER) && pepper_replaced "$pepper"; then
    if ! retired=$(setting_literal FI_API_SECRET_PEPPERS_RETIRED) \
      || ! retired=$(retired_with "$retired" "${pepper:-$PUBLISHED_PEPPER}"); then
      err "Cannot safely read FI_API_SECRET_PEPPERS_RETIRED in $ENV_FILE to add the previous FI_API_SECRET_PEPPER to it: write it as one literal value (entries without \$, quotes or escapes), then run setup again. Nothing was changed."
      exit 1
    fi
    new_pepper=""
    if [[ -z "$pepper" ]] && ! pepper_replaced "${FI_API_SECRET_PEPPER:-}"; then
      new_pepper=$FI_API_SECRET_PEPPER
    fi
    new_pepper=${new_pepper:-$(openssl rand -hex 32)}
    append_setting FI_API_SECRET_PEPPERS_RETIRED "$retired"
    append_setting FI_API_SECRET_PEPPER "$new_pepper"
    ok "Set a new FI_API_SECRET_PEPPER in $ENV_FILE and kept the previous value in FI_API_SECRET_PEPPERS_RETIRED; every other setting is unchanged."
    added=1
  fi
  if setting_missing INTEGRATION_ENCRYPTION_KEY; then
    INTEGRATION_ENCRYPTION_KEY=${INTEGRATION_ENCRYPTION_KEY:-$(openssl rand -base64 32 | tr '+/' '-_')}
    append_setting INTEGRATION_ENCRYPTION_KEY "$INTEGRATION_ENCRYPTION_KEY"
    ok "Added the missing INTEGRATION_ENCRYPTION_KEY to $ENV_FILE; every other setting is unchanged."
    added=1
  fi
  if setting_missing AGENTCC_WEBHOOK_SECRET; then
    AGENTCC_WEBHOOK_SECRET=${AGENTCC_WEBHOOK_SECRET:-$(openssl rand -hex 32)}
    append_setting AGENTCC_WEBHOOK_SECRET "$AGENTCC_WEBHOOK_SECRET"
    ok "Added the missing AGENTCC_WEBHOOK_SECRET to $ENV_FILE; every other setting is unchanged."
    added=1
  fi
  if (( ! added )); then
    say "Keeping existing $ENV_FILE unchanged; reuse its installed credentials."
  fi
  say "Other missing settings must be added explicitly; apart from a published API secret pepper, setup never rotates retained credentials."
else
  warn "New configuration only: for retained data, restore the original environment file instead."
  say ""
  say "${BOLD}== Public URLs ==${RESET}"
  FRONTEND_URL=$(prompt_required FRONTEND_URL "Frontend URL (e.g. https://app.example.com)")
  VITE_HOST_API=$(prompt_required VITE_HOST_API "Backend URL (e.g. https://api.example.com)")

  say ""
  say "${BOLD}== Release pins (one per image) ==${RESET}"
  FUTURE_AGI_VERSION=$(prompt_required FUTURE_AGI_VERSION "Backend release (futureagi/future-agi)")
  FRONTEND_VERSION=$(prompt_required FRONTEND_VERSION "Frontend release (futureagi/frontend)")
  FI_COLLECTOR_VERSION=$(prompt_required FI_COLLECTOR_VERSION "Reviewed collector release (futureagi/fi-collector; no local/latest)")
  case "$FI_COLLECTOR_VERSION" in
    local|latest|local@*|latest@*) err "FI_COLLECTOR_VERSION must select a reviewed release, not local/latest"; exit 1 ;;
  esac
  AGENTCC_GATEWAY_VERSION=$(prompt_required AGENTCC_GATEWAY_VERSION "Gateway release (futureagi/agentcc-gateway)")
  SERVING_VERSION=$(prompt_required SERVING_VERSION "Serving release (futureagi/serving)")
  CODE_EXECUTOR_VERSION=$(prompt_required CODE_EXECUTOR_VERSION "Code-executor release (futureagi/code-executor)")
  SIMULATION_RUNNER_VERSION=$(prompt_required SIMULATION_RUNNER_VERSION "Simulation SDK runner release (futureagi/future-agi-simulation-runner)")

  say ""
  say "${BOLD}== Omega Hub checker (deploy/hub-runner/README.md) ==${RESET}"
  say "Uploads are checked on a dedicated runner host whose Docker runs containers on gVisor."
  OMEGA_HUB_CHECKER_VERSION=$(prompt_required OMEGA_HUB_CHECKER_VERSION "Checker release (futureagi/omega-hub-checker)")
  OMEGA_HUB_RUNNER_IMAGE=$(prompt_required OMEGA_HUB_RUNNER_IMAGE "Runner image, pinned by digest (<repository>@sha256:...)")
  case "$OMEGA_HUB_RUNNER_IMAGE" in
    *@sha256:*) ;;
    *) err "OMEGA_HUB_RUNNER_IMAGE must be pinned by digest (<repository>@sha256:<digest>)"; exit 1 ;;
  esac
  OMEGA_HUB_RUNNER_DOCKER_HOST=$(prompt_required OMEGA_HUB_RUNNER_DOCKER_HOST "Runner host's Docker (tcp://<host>:2376)")
  case "$OMEGA_HUB_RUNNER_DOCKER_HOST" in
    tcp://?*) ;;
    *) err "OMEGA_HUB_RUNNER_DOCKER_HOST must be the runner host, tcp://<host>:2376, never this host's Docker socket"; exit 1 ;;
  esac
  OMEGA_HUB_RUNNER_TLS_DIR=$(prompt_required OMEGA_HUB_RUNNER_TLS_DIR "Directory with the runner host's client certificate (ca.pem, cert.pem, key.pem)")

  say ""
  say "${BOLD}== Catalog credentials ==${RESET}"
  say "Supply the separately provisioned reader/writer credentials; never substitute new passwords for retained indexes."
  PROPERTY_CATALOG_API_PASSWORD=$(prompt_required PROPERTY_CATALOG_API_PASSWORD "Existing/provisioned catalog reader password" 1)
  PROPERTY_CATALOG_CONSUMER_PASSWORD=$(prompt_required PROPERTY_CATALOG_CONSUMER_PASSWORD "Existing/provisioned catalog writer password" 1)

  say ""
  say "${BOLD}== Preparing new application secrets ==${RESET}"
  SECRET_KEY=${SECRET_KEY:-$(openssl rand -hex 32)}
  FI_API_SECRET_PEPPER=${FI_API_SECRET_PEPPER:-$(openssl rand -hex 32)}
  # Fernet key (32 random bytes, URL-safe base64): every organization's system
  # API key and stored integration credentials are encrypted under it.
  INTEGRATION_ENCRYPTION_KEY=${INTEGRATION_ENCRYPTION_KEY:-$(openssl rand -base64 32 | tr '+/' '-_')}
  AGENTCC_INTERNAL_API_KEY=${AGENTCC_INTERNAL_API_KEY:-$(openssl rand -hex 32)}
  AGENTCC_ADMIN_TOKEN=${AGENTCC_ADMIN_TOKEN:-$(openssl rand -hex 32)}
  # The gateway sends its request logs to the backend with it; both read it on start.
  AGENTCC_WEBHOOK_SECRET=${AGENTCC_WEBHOOK_SECRET:-$(openssl rand -hex 32)}
  # Shared by the backend and the Hub checker; both read it on start.
  OMEGA_HUB_CHECKER_TOKEN=${OMEGA_HUB_CHECKER_TOKEN:-$(openssl rand -hex 32)}
  PG_PASSWORD=${PG_PASSWORD:-$(openssl rand -hex 24)}
  MINIO_ROOT_PASSWORD=${MINIO_ROOT_PASSWORD:-$(openssl rand -hex 24)}
  ok "Prepared application secrets (explicit values preserved; missing values generated)"

  say ""
  say "${BOLD}== Optional LLM provider keys ==${RESET}"
  say "${DIM}Press Enter to skip any.${RESET}"
  OPENAI_API_KEY=${OPENAI_API_KEY:-$(prompt "OPENAI_API_KEY" "" 1)}
  ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-$(prompt "ANTHROPIC_API_KEY" "" 1)}
  GOOGLE_API_KEY=${GOOGLE_API_KEY:-$(prompt "GOOGLE_API_KEY" "" 1)}

  umask 077
  # Refuse a concurrent file creation rather than overwrite operator configuration.
  set -o noclobber
  {
  cat <<EOF
# Generated by deploy/setup.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ").
# Edit explicitly; re-running setup preserves this file and its credentials.

FUTURE_AGI_VERSION=${FUTURE_AGI_VERSION}
FRONTEND_VERSION=${FRONTEND_VERSION}
FI_COLLECTOR_VERSION=${FI_COLLECTOR_VERSION}
AGENTCC_GATEWAY_VERSION=${AGENTCC_GATEWAY_VERSION}
SERVING_VERSION=${SERVING_VERSION}
CODE_EXECUTOR_VERSION=${CODE_EXECUTOR_VERSION}
SIMULATION_RUNNER_VERSION=${SIMULATION_RUNNER_VERSION}
OMEGA_HUB_CHECKER_VERSION=${OMEGA_HUB_CHECKER_VERSION}

FRONTEND_URL=${FRONTEND_URL}
VITE_HOST_API=${VITE_HOST_API}
OMEGA_HUB_APP_URL=${FRONTEND_URL%/}/dashboard/hub
OMEGA_HUB_PUBLIC_API_URL=${VITE_HOST_API%/}/omega-hub/v1
# The Omega Hub runs while this lists hub: Compose then also needs
# -f deploy/docker-compose.production.hub.yml (setup and bin/update add it).
COMPOSE_PROFILES=hub

EOF
  for name in OMEGA_HUB_RUNNER_IMAGE OMEGA_HUB_RUNNER_DOCKER_HOST OMEGA_HUB_RUNNER_TLS_DIR; do
    printf '%s=' "$name"
    dotenv_literal "${!name}"
    printf '\n'
  done
  cat <<EOF

EOF
  for name in "${SECRET_NAMES[@]}"; do
    printf '%s=' "$name"
    dotenv_literal "${!name:-}"
    printf '\n'
  done
  } > "$ENV_FILE"
  set +o noclobber
  ok "Wrote $ENV_FILE (mode 600)"
fi

cd "$REPO_ROOT"
# The file is authoritative for installed credentials. Ambient exports
# must not shadow retained passwords or hide missing entries during validation.
unset "${SECRET_NAMES[@]}"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f docker-compose.distributed.yml -f deploy/docker-compose.production.yml)
# Hub operators (COMPOSE_PROFILES lists hub) add the Hub's production file right after it.
if hub_profile_enabled; then
  COMPOSE+=(-f deploy/docker-compose.production.hub.yml)
fi
# Do not print rendered environment/secrets, or execute/shell-source the env file.
# Compose's message for an unset required variable names only the variable; it is
# the one part of its output repeated here (any other line may quote a value).
if ! config_errors=$("${COMPOSE[@]}" config --quiet 2>&1 >/dev/null); then
  missing=$(printf '%s\n' "$config_errors" \
    | sed -nE 's/.*required variable ([A-Za-z_][A-Za-z0-9_]*) is missing a value.*/\1/p' \
    | sort -u | paste -sd ' ' -)
  if [[ -n "$missing" ]]; then
    err "Required setting missing from $ENV_FILE: $missing (deploy/README.md, 1. Generate secrets)."
  fi
  err "Invalid production configuration: check required credentials and every production image version. Existing credentials must be reused; see deploy/README.md."
  exit 1
fi
images=$("${COMPOSE[@]}" config --images 2>/dev/null) || { err "Cannot validate production image selection"; exit 1; }
collector_found=0
while IFS= read -r image; do
  case "$image" in
    futureagi/fi-collector:local|futureagi/fi-collector:latest|futureagi/fi-collector:local@*|futureagi/fi-collector:latest@*)
      err "FI_COLLECTOR_VERSION must select a reviewed release, not local/latest"; exit 1 ;;
    futureagi/fi-collector:*) collector_found=1 ;;
  esac
done <<< "$images"
(( collector_found )) || { err "Missing production collector image"; exit 1; }

if (( SKIP_UP )); then
  say ""
  ok "Setup complete. Skipping boot per --skip-up."
  exit 0
fi

if (( ! CONFIRM_INITIALIZED )); then
  err "Boot requires --confirm-initialized: separately approved PG migrations, native/index schemas, grants and compatible PeerDB mirrors must already exist. See deploy/README.md; setup never applies initialization."
  exit 1
fi

say ""
say "${BOLD}== Pulling images ==${RESET}"
"${COMPOSE[@]}" pull

say ""
say "${BOLD}== Starting stack ==${RESET}"
if ! "${COMPOSE[@]}" up -d --no-build --wait --wait-timeout 1200; then
  err "Startup/check-only validation failed; partial state retained. Inspect before explicitly resuming; no automatic retry or initialization."
  exit 1
fi

say ""
ok "Stack is up. Check status with:"
say "  ${COMPOSE[*]} ps"
say ""
say "Create your first user (interactive):"
say "  ${COMPOSE[*]} exec backend python manage.py create_user"
