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
fill_secret() { # var, generator
  if secret_is_unset "$1"; then
    set_env_var "$1" "$($2)"
    ok "Generated $1"
  fi
}

INSTALL_SECRETS=(
  SECRET_KEY
  PG_PASSWORD
  MINIO_ROOT_PASSWORD
  AGENTCC_INTERNAL_API_KEY
  AGENTCC_ADMIN_TOKEN
  CH_PASSWORD
)

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
# every value it has: Postgres stores its password in its volume, and a new
# SECRET_KEY or gateway key would sign everyone out. Only empty or CHANGEME-*
# keys are filled, and only on a fresh install. Three keys hold no state and
# are filled on any install: without INTEGRATION_ENCRYPTION_KEY every process
# starts with a random key of its own, so nothing encrypted before survives a
# restart anyway, the standalone install's Redis keeps nothing across
# restarts (the distributed stack's Redis takes no password), and the gateway
# and the app read AGENTCC_WEBHOOK_SECRET afresh on every start.
write_secrets() { # fresh (0|1), distributed (0|1), project
  local fresh="$1" distributed="$2" project="$3" var minio_pwd running_password
  local -a published_defaults=()
  if (( fresh == 1 )); then
    for var in "${INSTALL_SECRETS[@]}"; do
      fill_secret "$var" gen_secret
    done
    # S3_SECRET_KEY must match MINIO_ROOT_PASSWORD (S3 client speaks to MinIO).
    if grep -Eq "^S3_SECRET_KEY=CHANGEME-" .env; then
      minio_pwd=$(env_value MINIO_ROOT_PASSWORD "")
      sed_inplace -E "s|^S3_SECRET_KEY=CHANGEME-.*|S3_SECRET_KEY=${minio_pwd}|" .env
      ok "Aligned S3_SECRET_KEY with MINIO_ROOT_PASSWORD"
    fi
  else
    for var in "${INSTALL_SECRETS[@]}"; do
      if secret_is_unset "$var"; then published_defaults+=("$var"); fi
    done
    if (( ${#published_defaults[@]} > 0 )); then
      warn "Existing install: ${published_defaults[*]} still use the defaults published in this repository."
      warn "  The installer never changes an existing install's secrets. See https://docs.futureagi.com/docs/self-hosting/configuration/reference#1-generated-by-the-installer"
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
  fi
  fill_secret INTEGRATION_ENCRYPTION_KEY gen_fernet_key
  fill_secret AGENTCC_WEBHOOK_SECRET gen_secret
  if (( distributed == 0 )); then fill_secret REDIS_PASSWORD gen_secret; fi
}
