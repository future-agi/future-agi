# shellcheck shell=bash
# The secrets bin/install writes to .env. Sourced; needs env.sh and
# bin/install's output helpers (ok, warn) and preflight_fail.

gen_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import secrets; print(secrets.token_hex(32))'
  else
    LC_ALL=C tr -dc 'a-f0-9' </dev/urandom | head -c 64
    echo
  fi
}

# Fernet key (32 random bytes, URL-safe base64) for INTEGRATION_ENCRYPTION_KEY.
gen_fernet_key() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 32 | tr '+/' '-_'
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())'
  else
    head -c 32 /dev/urandom | base64 | tr '+/' '-_'
  fi
}

secret_is_unset() {
  local value
  value=$(env_value "$1" "")
  [[ -z "$value" || "$value" == CHANGEME-* ]]
}
fill_secret() { # var, generator, [a published default that counts as unset]
  if secret_is_unset "$1" || [[ -n "${3:-}" && "$(env_value "$1" "")" == "$3" ]]; then
    set_env_var "$1" "$($2)"
    ok "Generated $1"
  fi
}

INSTALL_SECRETS=(
  SECRET_KEY
  FI_API_SECRET_PEPPER
  PG_PASSWORD
  MINIO_ROOT_PASSWORD
  AGENTCC_INTERNAL_API_KEY
  CH_PASSWORD
)

# A deliberately bounded Compose dotenv reader for rotation, NOT shell source.
# Decode single-line quotes and inline comments. Reject interpolation/escapes we
# cannot resolve identically without Compose, and noncanonical target assignments
# that set_env_var cannot replace. Validate other lines' quote boundaries too:
# otherwise a multiline value could hide an assignment from our line-based writer.
pepper_env_value() { # VAR; returns nonzero rather than guessing on unsafe syntax
  local var="$1" line key raw decoded value="" quote
  local assignment='^[[:space:]]*(export[[:space:]]+)?([a-zA-Z_][a-zA-Z0-9_]*)[[:space:]]*=[[:space:]]*(.*)$'
  local single="^'([^']*)'[[:space:]]*(#.*)?$"
  local double='^"(([^"\\]|\\.)*)"[[:space:]]*(#.*)?$'
  [[ -f .env ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line=${line%$'\r'}
    [[ "$line" =~ ^[[:space:]]*(#.*)?$ ]] && continue
    [[ "$line" =~ $assignment ]] || return 1
    key=${BASH_REMATCH[2]} raw=${BASH_REMATCH[3]}
    if [[ "$key" == "$var" && "$line" != "$var="* ]]; then return 1; fi
    quote=${raw:0:1}
    case "$quote" in
      "'")
        [[ "$raw" =~ $single ]] || return 1
        decoded=${BASH_REMATCH[1]}
        # An escaped closing quote is not a closing boundary.
        [[ "$decoded" != *\\ ]] || return 1
        ;;
      '"')
        [[ "$raw" =~ $double ]] || return 1
        decoded=${BASH_REMATCH[1]}
        ;;
      *)
        # Only a hash preceded by whitespace inside the value starts a
        # comment. A hash at the beginning of the trimmed value is literal.
        decoded=${raw%%[[:space:]]#*}
        decoded="${decoded%"${decoded##*[![:space:]]}"}"
        ;;
    esac
    [[ "$key" == "$var" ]] || continue
    if [[ "$quote" != "'" && "$decoded" == *'$'* ]] ||
       [[ "$quote" == '"' && "$decoded" == *\\* ]] ||
       [[ "$quote" == "'" && "$decoded" == *\\\\* ]] ||
       [[ "$decoded" == *$'\r'* ]]; then return 1; fi
    value=$decoded
  done < .env
  printf '%s' "$value"
}

pepper_dotenv_error() {
  warn "Cannot safely decode FI_API_SECRET_PEPPER / FI_API_SECRET_PEPPERS_RETIRED from .env; use single-line literal assignments before rotation (no interpolation or escaped quotes). Peppers unchanged"
}

# Upgrades may already have API keys sealed under the compose default. Keep it
# available for verification before changing the active pepper. Unlike fresh
# install placeholders, any other nonempty effective value belongs to the operator.
rotate_published_api_secret_pepper() {
  local published=local-dev-only-api-secret-pepper-replace-me
  local current previous new_pepper part retired=""
  local -a entries=()
  # Compose prefers shell variables, even when exported empty. A custom active
  # override is operator-owned; a default/empty override would mask our new value.
  if [[ ${FI_API_SECRET_PEPPER+x} ]]; then
    if [[ -n "$FI_API_SECRET_PEPPER" && "$FI_API_SECRET_PEPPER" != "$published" ]]; then return 0; fi
    warn "Unset FI_API_SECRET_PEPPER in the shell before rotation; it overrides .env. Peppers unchanged"
    return 1
  fi
  current=$(pepper_env_value FI_API_SECRET_PEPPER) || { pepper_dotenv_error; return 1; }
  if [[ -n "$current" && "$current" != "$published" ]]; then return 0; fi
  # Do not silently edit only .env while the verifiers receive a different list.
  # Require one persisted source of truth, including for an empty shell override.
  if [[ ${FI_API_SECRET_PEPPERS_RETIRED+x} ]]; then
    warn "Unset FI_API_SECRET_PEPPERS_RETIRED in the shell before rotating FI_API_SECRET_PEPPER; it overrides .env. Peppers unchanged"
    return 1
  fi
  previous=$(pepper_env_value FI_API_SECRET_PEPPERS_RETIRED) || { pepper_dotenv_error; return 1; }
  # Both verifiers trim comma-separated entries. Dedupe decoded exact values in
  # first-occurrence order, including an already-retired default.
  IFS=, read -r -a entries < <(printf '%s\n' "${previous},${published}")
  for part in "${entries[@]}"; do
    part="${part#"${part%%[![:space:]]*}"}"
    part="${part%"${part##*[![:space:]]}"}"
    if [[ -n "$part" && ",$retired," != *,"$part",* ]]; then
      # Serialize as a literal single-quoted value. Do not guess at escapes.
      if [[ "$part" == *"'"* || "$part" == *\\\\* || "$part" == *\\ ]]; then
        pepper_dotenv_error; return 1
      fi
      retired+="${retired:+,}${part}"
    fi
  done
  new_pepper=$(gen_secret) || return $?
  if [[ ! "$new_pepper" =~ ^[0-9a-f]{64}$ ]]; then
    warn "Could not generate FI_API_SECRET_PEPPER; .env unchanged"
    return 1
  fi
  # Retire first so an interrupted rotation is safe to finish. Single quotes
  # keep hashes, dollar signs and comments literal when Compose reads the result.
  set_env_var FI_API_SECRET_PEPPERS_RETIRED "'$retired'"
  set_env_var FI_API_SECRET_PEPPER "$new_pepper"
  ok "Rotated FI_API_SECRET_PEPPER (published default moved to retired)"
}

# The password this project's clickhouse container runs with: empty when its
# environment has none, as on installs whose release ignored CH_PASSWORD.
# Fails when there is no container to tell by.
clickhouse_running_password() { # project
  local id container_env
  id=$(docker ps -aq --filter "label=com.docker.compose.project=$1" \
    --filter "label=com.docker.compose.service=clickhouse" 2>/dev/null | head -1)
  [[ -n "$id" ]] || return 1
  container_env=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$id" 2>/dev/null) \
    || return 1
  printf '%s\n' "$container_env" | sed -n 's/^CLICKHOUSE_PASSWORD=//p'
}

# A fresh install (no volumes of this project) gets its own secrets instead
# of the defaults published in the compose files. An existing install keeps
# operator-set values: Postgres stores its password in its volume, and a new
# SECRET_KEY or gateway key would sign everyone out. Its empty or published API
# pepper is rotated with the default retired, preserving existing API keys.
# Other empty or CHANGEME-* keys are filled only on a fresh install, except
# five keys that are safe to fill on any install: without
# INTEGRATION_ENCRYPTION_KEY every process starts with a
# random integration key of its own, so no integration credential encrypted
# before survives a restart anyway, and the system API keys sealed meanwhile
# under the key derived from SECRET_KEY still decrypt once it is set
# (futureagi/accounts/api_secrets.py); the standalone install's Redis keeps
# nothing across restarts (the distributed stack's Redis takes no password);
# the gateway and the app read AGENTCC_WEBHOOK_SECRET and AGENTCC_ADMIN_TOKEN
# afresh on every start, and so do the app and the Hub checker
# OMEGA_HUB_CHECKER_TOKEN. The gateway refuses to start on the admin token the
# compose files publish, so that default is replaced too.
write_secrets() { # fresh (0|1), distributed (0|1), project
  local fresh="$1" distributed="$2" project="$3" var minio_pwd running_password
  local -a published_defaults=()
  if (( fresh == 1 )); then
    for var in "${INSTALL_SECRETS[@]}"; do
      fill_secret "$var" gen_secret
    done
    # S3_SECRET_KEY must match MINIO_ROOT_PASSWORD (S3 client speaks to MinIO).
    # The line's text is copied as written, so Compose reads the same value.
    if grep -Eq "^S3_SECRET_KEY=CHANGEME-" .env; then
      minio_pwd=$(env_text MINIO_ROOT_PASSWORD)
      sed_inplace -E "s|^S3_SECRET_KEY=CHANGEME-.*|S3_SECRET_KEY=${minio_pwd}|" .env
      ok "Aligned S3_SECRET_KEY with MINIO_ROOT_PASSWORD"
    fi
  else
    for var in "${INSTALL_SECRETS[@]}"; do
      # Handled separately below, without changing operator-set peppers.
      [[ "$var" == FI_API_SECRET_PEPPER ]] && continue
      if secret_is_unset "$var"; then published_defaults+=("$var"); fi
    done
    if (( ${#published_defaults[@]} > 0 )); then
      warn "Existing install: ${published_defaults[*]} still use the defaults published in this repository."
      warn "  The installer keeps these existing secrets. An empty or published-default FI_API_SECRET_PEPPER is rotated with the default retired. See https://docs.futureagi.com/docs/self-hosting/configuration/reference#1-generated-by-the-installer"
    fi
    # ClickHouse takes CH_PASSWORD at every start, while Distributed's PeerDB
    # peer keeps the password it was set up with, and the bootstrap re-creates
    # the dictionaries with a new password but not without one. Neither value
    # is printed. "|| exit 1" keeps the ERR trap, which the substitution
    # inherits through set -E, from reporting "no ClickHouse container" as a
    # failed install.
    if running_password=$(clickhouse_running_password "$project" || exit 1) \
      && [[ "$running_password" != "$(compose_env_value CH_PASSWORD)" ]]; then
      preflight_fail "CH_PASSWORD differs from the password this install's ClickHouse runs with. Starting now would change that password, while what was set up with the old one keeps it: on Distributed the Postgres → ClickHouse sync, and, when CH_PASSWORD is now empty, the dictionaries every span insert reads. Set CH_PASSWORD back to the password ClickHouse runs with (empty on installs made before the installer generated one), or see INSTALLATION.md › Secrets that must be changed"
    fi
    rotate_published_api_secret_pepper
  fi
  fill_secret INTEGRATION_ENCRYPTION_KEY gen_fernet_key
  fill_secret AGENTCC_WEBHOOK_SECRET gen_secret
  fill_secret AGENTCC_ADMIN_TOKEN gen_secret local-dev-only-admin-token-replace-me
  # The app and the Hub checker read it on every start, so it is safe to fill
  # on any install; a check in flight is retried by the checker after a restart.
  fill_secret OMEGA_HUB_CHECKER_TOKEN gen_secret
  if (( distributed == 0 )); then fill_secret REDIS_PASSWORD gen_secret; fi
}
