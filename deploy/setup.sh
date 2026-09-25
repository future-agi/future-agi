#!/usr/bin/env bash
# Interactive production setup for self-hosted Future AGI.
# Preserves existing configuration; new files require supplied catalog credentials.
# Boot checks previously initialized schema/mirrors; it never initializes them.
#
# Usage: ./deploy/setup.sh [--skip-up] [--non-interactive] [--confirm-initialized]

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." &>/dev/null && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env.production"
SECRET_NAMES=(SECRET_KEY AGENTCC_INTERNAL_API_KEY AGENTCC_ADMIN_TOKEN PG_PASSWORD
  MINIO_ROOT_PASSWORD RABBITMQ_PASSWORD PROPERTY_CATALOG_API_PASSWORD
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

if [[ -f "$ENV_FILE" ]]; then
  say "Keeping existing $ENV_FILE unchanged; reuse its installed credentials."
  say "Missing settings must be added explicitly; setup never rotates retained credentials."
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
  say "${BOLD}== Catalog credentials ==${RESET}"
  say "Supply the separately provisioned reader/writer credentials; never substitute new passwords for retained indexes."
  PROPERTY_CATALOG_API_PASSWORD=$(prompt_required PROPERTY_CATALOG_API_PASSWORD "Existing/provisioned catalog reader password" 1)
  PROPERTY_CATALOG_CONSUMER_PASSWORD=$(prompt_required PROPERTY_CATALOG_CONSUMER_PASSWORD "Existing/provisioned catalog writer password" 1)

  say ""
  say "${BOLD}== Preparing new application secrets ==${RESET}"
  SECRET_KEY=${SECRET_KEY:-$(openssl rand -hex 32)}
  AGENTCC_INTERNAL_API_KEY=${AGENTCC_INTERNAL_API_KEY:-$(openssl rand -hex 32)}
  AGENTCC_ADMIN_TOKEN=${AGENTCC_ADMIN_TOKEN:-$(openssl rand -hex 32)}
  PG_PASSWORD=${PG_PASSWORD:-$(openssl rand -hex 24)}
  MINIO_ROOT_PASSWORD=${MINIO_ROOT_PASSWORD:-$(openssl rand -hex 24)}
  RABBITMQ_PASSWORD=${RABBITMQ_PASSWORD:-$(openssl rand -hex 24)}
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

RABBITMQ_USER=futureagi

FUTURE_AGI_VERSION=${FUTURE_AGI_VERSION}
FRONTEND_VERSION=${FRONTEND_VERSION}
FI_COLLECTOR_VERSION=${FI_COLLECTOR_VERSION}
AGENTCC_GATEWAY_VERSION=${AGENTCC_GATEWAY_VERSION}
SERVING_VERSION=${SERVING_VERSION}
CODE_EXECUTOR_VERSION=${CODE_EXECUTOR_VERSION}
SIMULATION_RUNNER_VERSION=${SIMULATION_RUNNER_VERSION}

FRONTEND_URL=${FRONTEND_URL}
VITE_HOST_API=${VITE_HOST_API}

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
COMPOSE=(docker compose --env-file "$ENV_FILE" -f docker-compose.yml -f deploy/docker-compose.production.yml)
# Do not print rendered environment/secrets, or execute/shell-source the env file.
if ! "${COMPOSE[@]}" config --quiet >/dev/null 2>&1; then
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
say "  docker compose --env-file $ENV_FILE -f docker-compose.yml -f deploy/docker-compose.production.yml ps"
say ""
say "Create your first user (interactive):"
say "  docker compose --env-file $ENV_FILE -f docker-compose.yml -f deploy/docker-compose.production.yml exec backend python manage.py create_user"
