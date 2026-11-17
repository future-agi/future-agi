# shellcheck shell=bash
# The secrets bin/install writes to .env. Sourced; needs env.sh and
# bin/install's output helpers (ok, warn, die) and preflight_fail.

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

# FI_API_SECRET_PEPPER is not here: write_secrets rotates it on every install.
INSTALL_SECRETS=(
  SECRET_KEY
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
  local var="$1" line key raw decoded value="" quote mcp_seen=0
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
    if [[ "$var" == AGENTCC_MCP_STATE_KEYS && "$key" == "$var" ]]; then
      (( mcp_seen == 0 )) || return 1
      mcp_seen=1
    fi
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

# MCP state keys are raw UTF-8 entries, not encoded secrets to rotate. Return
# only a new value (empty output means preserve every configured byte).
prepare_mcp_state_keys() {
  local current part space before generated count=0
  local -a entries=()
  # Go strings.TrimSpace's exact Unicode whitespace, independent of the locale.
  local -a spaces=($' ' $'\t' $'\n' $'\v' $'\f' $'\r' '' ' ' ' ' ' ' ' ' ' ' ' ' ' ' ' ' ' ' ' ' ' ' ' ' ' ' ' ' ' ' ' ' ' ' '　')
  if [[ ${AGENTCC_MCP_STATE_KEYS+x} ]]; then
    warn "Unset AGENTCC_MCP_STATE_KEYS in the shell; it overrides .env. Credentials unchanged"
    return 1
  fi
  if [[ -f .env ]]; then
    # Refuse invalid UTF-8 or NUL rather than letting a shell silently discard bytes.
    if ! iconv -f UTF-8 -t UTF-8 .env >/dev/null 2>&1 ||
       ! LC_ALL=C tr -d '\000' < .env | cmp -s .env -; then
      warn "Cannot safely read AGENTCC_MCP_STATE_KEYS; credentials unchanged"
      return 1
    fi
  fi
  current=$(pepper_env_value AGENTCC_MCP_STATE_KEYS) || {
    warn "Cannot safely read AGENTCC_MCP_STATE_KEYS; credentials unchanged"; return 1;
  }
  if [[ -n "$current" ]]; then
    IFS=, read -r -a entries < <(printf '%s\n' "$current")
    for part in "${entries[@]}"; do
      while :; do
        before=$part
        for space in "${spaces[@]}"; do
          part=${part#"$space"}
          part=${part%"$space"}
        done
        [[ "$part" != "$before" ]] || break
      done
      [[ -n "$part" ]] || continue
      if (( $(printf '%s' "$part" | LC_ALL=C wc -c) < 32 )); then
        warn "Invalid AGENTCC_MCP_STATE_KEYS; credentials unchanged"; return 1
      fi
      count=$((count + 1))
    done
    if (( count == 0 )); then
      warn "Invalid AGENTCC_MCP_STATE_KEYS; credentials unchanged"; return 1
    fi
    return 0
  fi
  generated=$(gen_secret) || {
    warn "Could not generate AGENTCC_MCP_STATE_KEYS; credentials unchanged"; return 1;
  }
  if [[ ! "$generated" =~ ^[0-9a-f]{64}$ ]]; then
    warn "Could not generate AGENTCC_MCP_STATE_KEYS; credentials unchanged"; return 1
  fi
  printf '%s' "$generated"
}

pepper_dotenv_error() {
  warn "Cannot safely decode FI_API_SECRET_PEPPER / FI_API_SECRET_PEPPERS_RETIRED from .env; use single-line literal assignments before rotation (no interpolation or escaped quotes). Peppers unchanged"
}

# Any install may already have API keys sealed under the pepper Compose used:
# the compose default when .env has none, else .env's value. An empty,
# published or CHANGEME-* pepper is replaced, with that value kept for
# verification first; any other nonempty effective value belongs to the operator.
rotate_published_api_secret_pepper() {
  local published=local-dev-only-api-secret-pepper-replace-me
  local current previous new_pepper part retired=""
  local -a entries=()
  # Compose prefers shell variables, even when exported empty. A custom active
  # override is operator-owned; a default/empty override would mask our new value.
  if [[ ${FI_API_SECRET_PEPPER+x} ]]; then
    if [[ -n "$FI_API_SECRET_PEPPER" && "$FI_API_SECRET_PEPPER" != "$published" \
      && "$FI_API_SECRET_PEPPER" != CHANGEME-* ]]; then return 0; fi
    warn "Unset FI_API_SECRET_PEPPER in the shell before rotation; it overrides .env. Peppers unchanged"
    return 1
  fi
  current=$(pepper_env_value FI_API_SECRET_PEPPER) || { pepper_dotenv_error; return 1; }
  if [[ -n "$current" && "$current" != "$published" && "$current" != CHANGEME-* ]]; then return 0; fi
  # Do not silently edit only .env while the verifiers receive a different list.
  # Require one persisted source of truth, including for an empty shell override.
  if [[ ${FI_API_SECRET_PEPPERS_RETIRED+x} ]]; then
    warn "Unset FI_API_SECRET_PEPPERS_RETIRED in the shell before rotating FI_API_SECRET_PEPPER; it overrides .env. Peppers unchanged"
    return 1
  fi
  previous=$(pepper_env_value FI_API_SECRET_PEPPERS_RETIRED) || { pepper_dotenv_error; return 1; }
  # Both verifiers trim comma-separated entries. Dedupe decoded exact values in
  # first-occurrence order, including an already-retired one.
  IFS=, read -r -a entries < <(printf '%s\n' "${previous},${current:-$published}")
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
  ok "Rotated FI_API_SECRET_PEPPER (the previous value moved to retired)"
}

# One integration key per install. A Standalone container that finds none in
# .env makes its own once and keeps it in its app-data volume
# (deploy/standalone/bin/start). Adopt that one when .env has none; stop when
# .env or the shell holds a different one, --force included: continuing would
# write new secrets under one key while the stored ones need the other. Runs
# before anything else is written. Returns 1 when no key may be filled in
# because the volume cannot be checked or read. The value is never printed.
reconcile_integration_key() { # project
  local volume="$1_app-data" kept configured inspect_error volumes
  # Docker's own answer for a missing volume means there is none, and so does
  # a volume list that answers without it (other engines word that answer
  # otherwise). Any other failure (daemon down, no permission) may hide a kept
  # key. In each probe "|| exit 1" keeps the ERR trap, which the substitution
  # inherits through set -E, from reporting a failed probe as a failed install.
  if ! inspect_error=$(docker volume inspect "$volume" 2>&1 >/dev/null || exit 1); then
    if [[ "$(printf '%s' "$inspect_error" | tr '[:upper:]' '[:lower:]')" == *"no such volume"* ]]; then
      return 0
    fi
    if volumes=$(docker volume ls -q 2>/dev/null || exit 1) \
      && [[ $'\n'"$volumes"$'\n' != *$'\n'"$volume"$'\n'* ]]; then
      return 0
    fi
    warn "Could not tell whether the volume $volume exists (docker volume inspect $volume failed; run it to see why), so INTEGRATION_ENCRYPTION_KEY is not added to .env and the app keeps using its own"
    return 1
  fi
  # busybox:1.37 is the installer's probe image (PROBE_IMAGE, set further down bin/install).
  if ! kept=$(docker run --rm --network none -v "$volume:/data:ro" busybox:1.37 \
    sh -c 'f=/data/.integration-encryption-key; if [ -s $f ]; then cat $f; fi' 2>/dev/null || exit 1); then
    warn "Could not read the volume $volume to compare INTEGRATION_ENCRYPTION_KEY with the key the app keeps there; no key is added to .env, and the app keeps using its own"
    return 1
  fi
  [[ -n "$kept" ]] || return 0
  for configured in "${INTEGRATION_ENCRYPTION_KEY:-}" "$(env_value INTEGRATION_ENCRYPTION_KEY "")"; do
    if [[ -n "$configured" && "$configured" != CHANGEME-* && "$configured" != "$kept" ]]; then
      die "Two different integration keys: INTEGRATION_ENCRYPTION_KEY in .env or your shell, and the key the app keeps in the volume $volume (/data/.integration-encryption-key). Stored secrets can be read with only one of them. Put the key that was in use into .env, unset INTEGRATION_ENCRYPTION_KEY in your shell if you set it there, and run the installer again"
    fi
  done
  if secret_is_unset INTEGRATION_ENCRYPTION_KEY; then
    set_env_var INTEGRATION_ENCRYPTION_KEY "$kept"
    ok "Adopted the INTEGRATION_ENCRYPTION_KEY the app keeps in the volume $volume"
  fi
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
# SECRET_KEY or gateway key would sign everyone out. The API pepper is decided
# the same way on every install, because an install whose volumes were not
# seen also counts as fresh and may have API keys hashed under the pepper
# Compose used: an unset, empty, published or CHANGEME-* pepper is rotated with
# that value retired first, and any other value is the operator's. On a new
# install the retired value verifies nothing, since no key is stored yet.
# Other empty or CHANGEME-* keys are filled only on a fresh install, except
# seven keys that are safe to fill on any install: INTEGRATION_ENCRYPTION_KEY,
# once reconcile_integration_key has adopted the key a Standalone container
# kept for itself, since otherwise no integration credential was stored under
# a lasting key, and the system API keys sealed meanwhile
# under the key derived from SECRET_KEY still decrypt once it is set
# (futureagi/accounts/api_secrets.py); the standalone install's Redis keeps
# nothing across restarts (the distributed stack's Redis takes no password);
# the gateway and the app read AGENTCC_WEBHOOK_SECRET and AGENTCC_ADMIN_TOKEN
# afresh on every start, and so do the app and the Hub checker
# OMEGA_HUB_CHECKER_TOKEN. The gateway refuses to start on the admin token the
# compose files publish, so that default is replaced too. Distributed's PeerDB
# UI reads PEERDB_UI_PASSWORD (its sign-in password) and PEERDB_UI_NEXTAUTH_SECRET
# (its session key) at every start and stores neither, so on a Distributed
# install both are filled whether it is new or not; a new session key only signs
# the UI's users in again. Standalone runs no PeerDB, so its .env gets neither.
write_secrets() { # fresh (0|1), distributed (0|1), project
  local fresh="$1" distributed="$2" project="$3" var minio_pwd running_password fill_key=1 mcp_state_key
  local -a published_defaults=()
  mcp_state_key=$(prepare_mcp_state_keys) || return 1
  reconcile_integration_key "$project" || fill_key=0
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
      if secret_is_unset "$var"; then published_defaults+=("$var"); fi
    done
    if (( ${#published_defaults[@]} > 0 )); then
      warn "Existing install: ${published_defaults[*]} still use the defaults published in this repository."
      warn "  The installer keeps these existing secrets. An empty, published-default or CHANGEME- FI_API_SECRET_PEPPER is rotated with the previous value retired. See https://docs.futureagi.com/docs/self-hosting/configuration/reference#1-generated-by-the-installer"
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
  if [[ -n "$mcp_state_key" ]]; then
    # The existing append helper expects the preceding assignment to end in LF.
    if [[ -s .env && -n "$(tail -c 1 .env)" ]]; then printf '\n' >> .env || return 1; fi
    set_env_var AGENTCC_MCP_STATE_KEYS "$mcp_state_key" || return 1
    ok "Generated AGENTCC_MCP_STATE_KEYS"
  fi
  rotate_published_api_secret_pepper
  if (( fill_key == 1 )); then fill_secret INTEGRATION_ENCRYPTION_KEY gen_fernet_key; fi
  fill_secret AGENTCC_WEBHOOK_SECRET gen_secret
  fill_secret AGENTCC_ADMIN_TOKEN gen_secret local-dev-only-admin-token-replace-me
  # The app and the Hub checker read it on every start, so it is safe to fill
  # on any install; a check in flight is retried by the checker after a restart.
  fill_secret OMEGA_HUB_CHECKER_TOKEN gen_secret
  if (( distributed == 1 )); then
    fill_secret PEERDB_UI_PASSWORD gen_secret peerdb
    fill_secret PEERDB_UI_NEXTAUTH_SECRET gen_secret
  fi
  if (( distributed == 0 )); then fill_secret REDIS_PASSWORD gen_secret; fi
}
