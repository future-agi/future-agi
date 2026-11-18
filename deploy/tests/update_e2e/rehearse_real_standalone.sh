#!/usr/bin/env bash
# shellcheck disable=SC2317  # cleanup functions are reached through traps
# ============================================================================
# Real-stack rehearsal of a user's Standalone update with bin/update.
#
# Not part of CI. A maintainer runs it by hand, on a machine with Docker, to
# rehearse exactly what a self-hosted user does, with the real published
# futureagi/standalone images, the real ./bin/install and real data:
#
#   1. copy bin/update, deploy/update/ and the manifest generator from this
#      checkout once (every overlay uses that copy, so edits made while the
#      rehearsal runs cannot mix two versions), clone this repository, check
#      out FROM_VERSION (default v1.51.0) and, when that release does not ship
#      bin/update, overlay the copy (simulates a release that ships it)
#   2. create .env (FROM_VERSION, project name, non-default ports) and run
#      the real installer non-interactively; the UI, /health/ and a sign-in
#      of the created account must work
#   3. create a cosign key pair and a SIGNED manifest for TO_VERSION (default
#      v1.52.0) with scripts/generate_update_manifest.py, the real registry
#      digests of all nine components and the Helm chart, and the deployment
#      files of a TO_VERSION tree (with the same overlay); write update.conf
#      (trust that key, offline) and a pg_dump backup hook
#   4. ./bin/update check while the checkout is still at FROM_VERSION: must be
#      blocked (exit 3) naming the deployment drift and `git checkout`;
#      ./bin/update apply must refuse too, without running the backup
#   5. git checkout TO_VERSION, re-overlay, ./bin/install --no-up -y (must not
#      restart anything), check (update available, verified), a tampered
#      manifest (refused), apply --dry-run (changes nothing), apply; then the
#      backup, the receipt, the .env diff (FUTURE_AGI_VERSION only), the
#      running image digest (= the signed one), /health/, the UI, the same
#      account signing in, and check/apply reporting "up to date"; then a
#      later ./bin/install -y of the pinned install, which must start nothing
#      new and keep the signed image (Standalone builds nothing)
#   6. ./bin/update status
#   7. ./bin/update rollback, with and without --yes (the latter run from
#      outside the checkout, so its commands start with `cd <checkout> && `):
#      both refused while the checkout has TO_VERSION's deployment files,
#      naming the `git checkout FROM_VERSION -- <files>` that restores them,
#      and changing nothing. That command is pasted as printed; rollback
#      without --yes then asks for --yes, and the printed rollback --yes
#      command, pasted, rolls back to FROM_VERSION (real migrations may stop
#      the older app: the logs are kept); a second rollback is refused. Then
#      check is blocked by those files, and the printed "before updating
#      again" command, pasted, gives the checkout TO_VERSION's files back
#   8. if the rollback worked: update again in two steps. The TO_VERSION image
#      is untagged first (a rehearsal tag keeps its layers), so the stage has
#      to pull it. ./bin/update apply --stage must back up, pin .env, pull and
#      verify the signed digest and restart nothing; status and check must
#      show the staged update; ./bin/update apply must then take no second
#      backup and pass the same checks as the first apply (receipt, .env,
#      running digest, postgres/clickhouse kept, sign-in, data, up to date)
#   9. always: docker compose down -v of the rehearsal project, the untagged
#      image tagged again, and a check that no container, volume, network or
#      image tag of the project is left
#
# Usage (from anywhere; paths are absolute):
#
#   deploy/tests/update_e2e/rehearse_real_standalone.sh            # full run
#   REHEARSAL_COMPOSE=$HOME/bin/docker-compose-v2.24.0 deploy/tests/update_e2e/rehearse_real_standalone.sh
#       the full run with that Compose binary (see REHEARSAL_COMPOSE below)
#   deploy/tests/update_e2e/rehearse_real_standalone.sh --prepare-only
#       steps 1 and the Docker-free half of 3 (clone, overlay, .env, digests
#       over the registry HTTP API, unsigned manifest); starts nothing
#   deploy/tests/update_e2e/rehearse_real_standalone.sh --cleanup-only
#       step 9 alone (after a KEEP_STACK=1 run)
#
# Requirements: Docker with Compose v2 (`docker compose` or a v2+
# `docker-compose`), at least ~6 GB for the Docker VM, python3 >= 3.9, git,
# curl, network access to Docker Hub and ghcr.io. REHEARSAL_BASE must be a
# directory the Docker VM can bind-mount (Colima/Docker Desktop: under $HOME).
#
# Settings (environment):
#   REHEARSAL_BASE       work directory           (default: $HOME/fagi-real-rehearsal)
#   REHEARSAL_PROJECT    Compose project name     (default: fagi-real-rehearsal;
#                        must start with fagi-real-rehearsal, so cleanup can
#                        never touch another project)
#   REHEARSAL_SOURCE     repository to clone      (default: this checkout)
#   REHEARSAL_FALLBACK_SOURCE  clone this if REHEARSAL_SOURCE cannot be cloned
#   REHEARSAL_UPDATER    checkout with bin/update, deploy/update/ and
#                        scripts/generate_update_manifest.py (default: this one)
#   FROM_VERSION / TO_VERSION                     (default: v1.51.0 / v1.52.0)
#   FRONTEND_PORT BACKEND_PORT FI_COLLECTOR_OTLP_PORT FI_COLLECTOR_OTLP_HTTP_PORT
#   AGENTCC_GATEWAY_PORT MINIO_API_PORT           (default: 23000 28000 24317
#                        24318 28090 29005; the ports bin/install checks)
#   REHEARSAL_ADMIN_EMAIL                         (default: admin@example.com)
#   REHEARSAL_READY_MAX_SECONDS  INSTALL_READY_MAX_SECONDS written to .env, so
#                        the installer and bin/update wait longer for the
#                        first migrations on a slow host (300-7200; default:
#                        unset, the installer's own default)
#   KEEP_STACK=1        skip step 9 (inspect the stack; then run the
#                        --cleanup-only command it prints, same settings)
#   REHEARSAL_COMPOSE    a docker-compose binary (say Compose v2.24.0, the
#                        documented minimum) for every Compose call of the run;
#                        default: `docker compose`, else `docker-compose`. A
#                        private bin directory (the run's compose-bin/) goes
#                        first on PATH, with `docker-compose` (a symlink to the
#                        binary) and a `docker` shim that runs the binary for
#                        `docker compose ...` and the real docker for anything
#                        else, so bin/install, bin/update, the backup hook and
#                        this script all use it, whichever way they probe
#   COSIGN_IMAGE CRANE_IMAGE                      (pinned cosign; crane:latest)
#
# Output: $REHEARSAL_BASE/runs/<UTC stamp>-<pid>/ holds one log per command,
# results.tsv (PASS/FAIL per check) and summary.txt (with the Compose version
# and the duration); the script exits non-zero when any check failed. The
# admin password and the cosign key password are generated per run into
# $REHEARSAL_BASE/secrets/ (mode 0600).
# ============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
THIS_REPO="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"

MODE=full
case "${1:-}" in
  "") ;;
  --prepare-only) MODE=prepare ;;
  --cleanup-only) MODE=cleanup ;;
  -h|--help) awk '/^# =+$/ { n++; next } n == 1' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "unknown argument: $1 (try --help)" >&2; exit 2 ;;
esac

BASE="${REHEARSAL_BASE:-$HOME/fagi-real-rehearsal}"
PROJECT="${REHEARSAL_PROJECT:-fagi-real-rehearsal}"
SOURCE="${REHEARSAL_SOURCE:-$THIS_REPO}"
FALLBACK_SOURCE="${REHEARSAL_FALLBACK_SOURCE:-}"
UPDATER_SRC="${REHEARSAL_UPDATER:-$THIS_REPO}"
FROM_VERSION="${FROM_VERSION:-v1.51.0}"
TO_VERSION="${TO_VERSION:-v1.52.0}"
ADMIN_EMAIL="${REHEARSAL_ADMIN_EMAIL:-admin@example.com}"
ADMIN_NAME="Rehearsal Admin"
COMPOSE_BINARY="${REHEARSAL_COMPOSE:-}"
COSIGN_IMAGE="${COSIGN_IMAGE:-ghcr.io/sigstore/cosign/cosign:v3.1.3@sha256:9e5c2f2edc34351160407ca3416c61855bdf9403c3c5936e0f0be7fc261611b8}"
CRANE_IMAGE="${CRANE_IMAGE:-gcr.io/go-containerregistry/crane:latest}"
HELM_CHART="oci://ghcr.io/future-agi/charts/futureagi"

# VAR=value pairs bin/install's port preflight checks for the Standalone stack.
PORT_SETTINGS="FRONTEND_PORT=${FRONTEND_PORT:-23000}
BACKEND_PORT=${BACKEND_PORT:-28000}
FI_COLLECTOR_OTLP_PORT=${FI_COLLECTOR_OTLP_PORT:-24317}
FI_COLLECTOR_OTLP_HTTP_PORT=${FI_COLLECTOR_OTLP_HTTP_PORT:-24318}
AGENTCC_GATEWAY_PORT=${AGENTCC_GATEWAY_PORT:-28090}
MINIO_API_PORT=${MINIO_API_PORT:-29005}"
UI_PORT="${FRONTEND_PORT:-23000}"
API_PORT="${BACKEND_PORT:-28000}"

# component=repository[:tag suffix], the manifest's nine components.
COMPONENTS="standalone=futureagi/standalone
backend=futureagi/future-agi
frontend=futureagi/frontend
fi_collector=futureagi/fi-collector
agentcc_gateway=futureagi/agentcc-gateway
serving=futureagi/serving
serving_gpu=futureagi/serving:-gpu
code_executor=futureagi/code-executor
simulation_runner=futureagi/future-agi-simulation-runner"

RELEASE_IMAGE="futureagi/standalone:$TO_VERSION"
KEEP_REF="$PROJECT-keep:standalone-$TO_VERSION"

CHECKOUT="$BASE/checkout"
RELEASE_SRC="$BASE/release-src-$TO_VERSION"
RELEASE_DIR="$BASE/release-$TO_VERSION"
KEYS="$BASE/keys"
SECRETS="$BASE/secrets"
BACKUPS="$BASE/backups"
HOOK="$BASE/backup-hook.sh"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
STARTED_AT=$(date +%s)
RUN_DIR="$BASE/runs/$STAMP-$$"
RESULTS="$RUN_DIR/results.tsv"
PASSES=0
FAILS=0
STACK_STARTED=0
DOCKER_OK=0
DC=()
COMPOSE_VERSION=""
COMPOSE_SOURCE=""
SIGNED_DIGEST=""
FROM_DIGEST=""
ADMIN_PASSWORD=""

# ------------------------------------------------------------- helpers --
log() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$RUN_DIR/rehearsal.log" >&2; }
die() { log "ABORT: $*"; exit 2; }

record() { # PASS|FAIL|INFO, step, check, detail
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "${4:-}" >>"$RESULTS"
  case "$1" in PASS) PASSES=$((PASSES + 1)) ;; FAIL) FAILS=$((FAILS + 1)) ;; esac
  log "[$1] step $2: $3${4:+ — $4}"
}
check() { # step, check, detail, command...: PASS when the command succeeds
  local step=$1 name=$2 detail=$3
  shift 3
  if "$@"; then record PASS "$step" "$name" "$detail"; else record FAIL "$step" "$name" "$detail"; fi
}

# run LOG CMD...: run in the checkout, output to RUN_DIR/LOG; sets RC and ELAPSED.
# run_in DIR LOG CMD...: the same in DIR.
RC=0
ELAPSED=0
run() { run_in "$CHECKOUT" "$@"; }
run_in() {
  local dir=$1 logf="$RUN_DIR/$2" start
  shift 2
  { [[ "$dir" == "$CHECKOUT" ]] || printf '(in %s)\n' "$dir"; printf '$'; printf ' %q' "$@"; printf '\n'; } >"$logf"
  start=$(date +%s)
  (cd "$dir" && "$@") </dev/null >>"$logf" 2>&1
  RC=$?
  ELAPSED=$(($(date +%s) - start))
  printf '[exit %s after %ss]\n' "$RC" "$ELAPSED" >>"$logf"
  log "  $(basename "$logf"): exit $RC in ${ELAPSED}s"
}
# paste LOG DIR COMMAND: COMMAND exactly as bin/update printed it, given to a
# shell in DIR (the directory bin/update ran in), as an operator pastes it.
paste_command() {
  if [[ -z "$3" ]]; then
    printf 'nothing to paste: bin/update printed no such command\n' >"$RUN_DIR/$1"
    RC=127
    log "  $1: nothing to paste"
    return
  fi
  run_in "$2" "$1" bash -c "$3"
}
contains() { grep -qF -- "$2" "$RUN_DIR/$1"; }
# The first `command` that LOG prints right after TEXT (bin/update quotes the
# commands it tells the operator to run in backticks).
printed_command() { # log, text
  python3 - "$RUN_DIR/$1" "$2" <<'PY'
import re, sys
found = re.search(re.escape(sys.argv[2]) + r"`([^`]+)`", open(sys.argv[1], encoding="utf-8", errors="replace").read())
print(found.group(1)) if found else sys.exit(1)
PY
}
py_quote() { python3 -c 'import shlex, sys; print(shlex.quote(sys.argv[1]))' "$1"; }

env_get() { # file, key: the last uncommented KEY=value, unquoted
  python3 - "$1" "$2" <<'PY'
import re, sys
value = ""
for line in open(sys.argv[1], encoding="utf-8").read().splitlines():
    m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
    if m and m.group(1) == sys.argv[2] and not line.lstrip().startswith("#"):
        value = m.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
print(value)
PY
}
env_set() { # file, key, value: replace the last KEY= line, or append
  python3 - "$1" "$2" "$3" <<'PY'
import os, re, sys
path, key, value = sys.argv[1:4]
lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
hit = None
for i, line in enumerate(lines):
    if not line.lstrip().startswith("#") and re.match(r"^\s*(?:export\s+)?%s\s*=" % re.escape(key), line):
        hit = i
if hit is None:
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(f"{key}={value}\n")
else:
    lines[hit] = f"{key}={value}\n"
mode = os.stat(path).st_mode & 0o777
with open(path, "w", encoding="utf-8") as handle:
    handle.write("".join(lines))
os.chmod(path, mode)
PY
}
env_changes() { # before, after: changed lines, secret-looking values redacted
  python3 - "$1" "$2" <<'PY'
import difflib, re, sys
a = open(sys.argv[1], encoding="utf-8").read().splitlines()
b = open(sys.argv[2], encoding="utf-8").read().splitlines()
secret = re.compile(r"(PASSWORD|SECRET|TOKEN|KEY|CREDENTIAL|DSN|LICENSE)", re.I)
for line in difflib.unified_diff(a, b, lineterm="", n=0):
    if line.startswith(("---", "+++", "@@")):
        continue
    key, sep, _ = line[1:].partition("=")
    print(line[0] + (key + "=<redacted>" if sep and secret.search(key) else line[1:]))
PY
}

compose_cmd() {
  if docker compose version >/dev/null 2>&1; then
    DC=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    DC=(docker-compose)
  else
    return 1
  fi
}
sh_quote() { local q="'\\''"; printf "'%s'" "${1//\'/$q}"; }
# The command that runs step 9 alone for this run's settings, ready to paste.
cleanup_command() {
  local var
  for var in DOCKER_CONTEXT REHEARSAL_BASE REHEARSAL_PROJECT; do
    [[ -z "${!var:-}" ]] || printf '%s=%s ' "$var" "$(sh_quote "${!var}")"
  done
  [[ -z "$COMPOSE_BINARY" ]] || printf 'REHEARSAL_COMPOSE=%s ' "$(sh_quote "$COMPOSE_BINARY")"
  printf '%s --cleanup-only\n' "$(sh_quote "$SCRIPT_DIR/$(basename -- "$0")")"
}
# REHEARSAL_COMPOSE: RUN_DIR/compose-bin first on PATH, with `docker-compose`
# (a symlink to the binary) and a `docker` shim that runs the binary for
# `docker compose ...` and the real docker for anything else. Only `compose`
# as the first argument is redirected: nothing here passes docker global
# options before it.
use_compose_binary() {
  local binary real_docker shim_dir="$RUN_DIR/compose-bin"
  binary=$(cd -- "$(dirname -- "$COMPOSE_BINARY")" 2>/dev/null && pwd)/$(basename -- "$COMPOSE_BINARY")
  [[ -f "$binary" && -x "$binary" ]] || die "REHEARSAL_COMPOSE=$COMPOSE_BINARY is not an executable file"
  COMPOSE_BINARY=$binary
  real_docker=$(command -v docker) || die "docker is not on PATH (REHEARSAL_COMPOSE needs the real docker CLI)"
  if ! mkdir -p "$shim_dir" || ! ln -sf "$binary" "$shim_dir/docker-compose"; then
    die "cannot create $shim_dir"
  fi
  cat >"$shim_dir/docker" <<SHIM || die "cannot write $shim_dir/docker"
#!/bin/sh
# rehearse_real_standalone.sh (REHEARSAL_COMPOSE): \`docker compose ...\` runs
# $binary, any other command the real docker.
if [ "\$1" = compose ]; then
  shift
  exec $(sh_quote "$binary") "\$@"
fi
exec $(sh_quote "$real_docker") "\$@"
SHIM
  chmod 0755 "$shim_dir/docker" || die "cannot make $shim_dir/docker executable"
  PATH="$shim_dir:$PATH"
  export PATH
  COMPOSE_SOURCE="REHEARSAL_COMPOSE=$binary (\`docker compose\` and \`docker-compose\` both run it, via $shim_dir)"
}

# Containers of the project: "service id started_at image_id", sorted.
project_containers() {
  local ids
  ids=$(docker ps -aq --no-trunc --filter "label=com.docker.compose.project=$PROJECT")
  [[ -n "$ids" ]] || return 0
  # shellcheck disable=SC2086
  docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}} {{.Id}} {{.State.StartedAt}} {{.Image}}' $ids | sort
}
app_container() {
  docker ps -q --no-trunc --filter "label=com.docker.compose.project=$PROJECT" \
    --filter "label=com.docker.compose.service=app" | head -n 1
}
# The app container's image: "id|version label|repo digests|Config.Image".
app_image_facts() {
  local cid image
  cid=$(app_container)
  [[ -n "$cid" ]] || { echo "no running app container"; return 1; }
  image=$(docker inspect --format '{{.Image}}|{{.Config.Image}}' "$cid") || return 1
  docker image inspect --format \
    "{{.Id}}|{{index .Config.Labels \"org.opencontainers.image.version\"}}|{{json .RepoDigests}}|${image#*|}" \
    "${image%%|*}"
}

http_ok() { curl -fsS -o /dev/null --max-time 10 "$1" 2>/dev/null; }
wait_http() { # url, seconds
  local deadline=$(($(date +%s) + $2))
  until http_ok "$1"; do
    (($(date +%s) < deadline)) || return 1
    sleep 5
  done
}
sign_in() { # prints an access token for the rehearsal account
  python3 - "$ADMIN_EMAIL" "$ADMIN_PASSWORD" <<'PY' |
import json, sys
print(json.dumps({"email": sys.argv[1], "password": sys.argv[2]}))
PY
    curl -fsS --max-time 30 -X POST "http://localhost:$API_PORT/accounts/token/" \
      -H 'Content-Type: application/json' --data-binary @- |
    python3 -c 'import json, sys; t = json.load(sys.stdin).get("access"); print(t) if t else sys.exit(1)'
}
# sha256 prefix of the account's API key: the same value before and after an
# update shows the organization's rows survived (the key itself is not logged).
api_key_fingerprint() { # token
  curl -fsS --max-time 30 "http://localhost:$API_PORT/accounts/keys/" -H "Authorization: Bearer $1" |
    python3 -c 'import hashlib, json, sys
d = json.load(sys.stdin).get("data") or {}
k = d.get("api_key") if isinstance(d, dict) else None
print(hashlib.sha256(k.encode()).hexdigest()[:16]) if k else sys.exit(1)'
}
account_checks() { # step, label: UI, /health/, sign-in; sets FINGERPRINT
  local step=$1 label=$2 token
  check "$step" "$label: /health/ answers" "http://localhost:$API_PORT/health/" wait_http "http://localhost:$API_PORT/health/" 120
  check "$step" "$label: UI answers" "http://localhost:$UI_PORT/" wait_http "http://localhost:$UI_PORT/" 120
  FINGERPRINT=""
  if token=$(sign_in) && [[ -n "$token" ]]; then
    record PASS "$step" "$label: $ADMIN_EMAIL signs in (POST /accounts/token/)"
    FINGERPRINT=$(api_key_fingerprint "$token") || FINGERPRINT=""
    record INFO "$step" "$label: API key fingerprint" "${FINGERPRINT:-unavailable}"
  else
    record FAIL "$step" "$label: $ADMIN_EMAIL signs in (POST /accounts/token/)"
  fi
}
# The app runs VERSION from the image with DIGEST (when given).
expect_running() { # step, label, version, digest
  local facts
  facts=$(app_image_facts) || { record FAIL "$1" "$2: app container running" "$facts"; return; }
  record INFO "$1" "$2: app image" "$facts"
  check "$1" "$2: running image is labelled $3" "$facts" test "$(cut -d'|' -f2 <<<"$facts")" = "$3"
  check "$1" "$2: container runs futureagi/standalone:$3" "$facts" test "$(cut -d'|' -f4 <<<"$facts")" = "futureagi/standalone:$3"
  if [[ -n "${4:-}" ]]; then
    check "$1" "$2: running image digest is $4" "$facts" grep -qF "futureagi/standalone@$4" <<<"$facts"
  fi
}

# Image digest of REF (repo:tag, Docker Hub or ghcr.io): crane, else the
# registry's HTTP API (anonymous; HEAD requests do not count against limits).
registry_digest() {
  python3 - "$1" <<'PY'
import json, sys, urllib.request
ref = sys.argv[1]
name, tag = ref.rsplit(":", 1)
if name.startswith("ghcr.io/"):
    registry, repo = "ghcr.io", name[len("ghcr.io/"):]
    token_url = f"https://ghcr.io/token?scope=repository:{repo}:pull"
else:
    registry, repo = "registry-1.docker.io", name
    token_url = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull"
token = json.load(urllib.request.urlopen(token_url, timeout=30))["token"]
accept = ", ".join([
    "application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"])
request = urllib.request.Request(f"https://{registry}/v2/{repo}/manifests/{tag}", method="HEAD",
                                 headers={"Authorization": f"Bearer {token}", "Accept": accept})
print(urllib.request.urlopen(request, timeout=30).headers["Docker-Content-Digest"])
PY
}
resolve_digest() {
  local ref=$1 out=""
  if command -v crane >/dev/null 2>&1; then
    out=$(crane digest "$ref" 2>/dev/null)
  elif ((DOCKER_OK == 1)); then
    out=$(docker run --rm --name "$PROJECT-crane-$$-$RANDOM" "$CRANE_IMAGE" digest "$ref" 2>/dev/null)
  fi
  if [[ ! "$out" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    out=$(registry_digest "$ref" 2>/dev/null)
  fi
  [[ "$out" =~ ^sha256:[0-9a-f]{64}$ ]] || return 1
  printf '%s\n' "$out"
}

# Copy bin/update and deploy/update/ into DEST when its release lacks them.
overlay_updater() {
  local dest=$1
  if [[ -d "$dest/.git" ]] && [[ -n "$(git -C "$dest" ls-files -- bin/update)" ]]; then
    log "  $(git -C "$dest" describe --tags 2>/dev/null) ships bin/update: no overlay"
    return 0
  fi
  install -m 0755 "$UPDATER_SRC/bin/update" "$dest/bin/update" || return 1
  rm -rf "$dest/deploy/update" && mkdir -p "$dest/deploy/update" || return 1
  (cd "$UPDATER_SRC/deploy/update" && tar --exclude='__pycache__' -cf - .) | (cd "$dest/deploy/update" && tar -xf -)
}
# Copy the updater under test from SRC into DEST once; its Python must parse.
snapshot_updater() {
  local src=$1 dest=$2
  rm -rf "$dest" && mkdir -p "$dest/bin" "$dest/scripts" "$dest/deploy/update" || return 1
  install -m 0755 "$src/bin/update" "$dest/bin/update" || return 1
  install -m 0755 "$src/scripts/generate_update_manifest.py" "$dest/scripts/" || return 1
  (cd "$src/deploy/update" && tar --exclude='__pycache__' -cf - .) | (cd "$dest/deploy/update" && tar -xf -) || return 1
  python3 - "$dest" <<'PY'
import ast, pathlib, sys
for path in sorted(pathlib.Path(sys.argv[1]).rglob("*.py")):
    ast.parse(path.read_text(encoding="utf-8"), str(path))
PY
}
tree_hash() { # dir: sha256 prefix over its files' paths and bytes
  python3 - "$1" <<'PY'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
digest = hashlib.sha256()
for path in sorted(p for p in root.rglob("*") if p.is_file()):
    digest.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes() + b"\0")
print(digest.hexdigest()[:16])
PY
}

count_backups() { find "$BACKUPS" -type f | wc -l | tr -d ' '; }
newest_receipt() { # marker: the newest receipt written after RUN_DIR/marker
  find "$CHECKOUT/.futureagi-updates" -name 'receipt-*.json' -newer "$RUN_DIR/$1" 2>/dev/null | sort | tail -n 1
}
receipt_is() { # receipt, Python expression over the receipt `r` (name(path): its file name): true?
  python3 - "$1" "$2" <<'PY' 2>/dev/null
import json, sys
r = json.load(open(sys.argv[1], encoding="utf-8"))
sys.exit(0 if eval(sys.argv[2], {"r": r, "name": lambda path: str(path).rsplit("/", 1)[-1]}) else 1)
PY
}
receipt_leaks() { # env file, receipt: prints the .env keys whose secret values the receipt holds
  python3 - "$1" "$2" <<'PY'
import re, sys
text = open(sys.argv[2]).read()
leaks = []
for line in open(sys.argv[1]).read().splitlines():
    key, sep, value = line.partition("=")
    if sep and re.search(r"(PASSWORD|SECRET|TOKEN|_KEY|KEY_|CREDENTIAL|DSN|PRIVATE|LICENSE)", key, re.I) \
            and len(value) >= 6 and value in text:
        leaks.append(key)
print("leaks:", leaks)
sys.exit(1 if leaks else 0)
PY
}
kept_containers() { # step, before list, after list: postgres and clickhouse are not recreated
  local service
  for service in postgres clickhouse; do
    check "$1" "$service container kept (not recreated)" "" \
      test "$(grep "^$service " "$RUN_DIR/$2" | cut -d' ' -f2)" = "$(grep "^$service " "$RUN_DIR/$3" | cut -d' ' -f2)"
  done
}
env_mode() { stat -f %Lp "$1" 2>/dev/null || stat -c %a "$1"; }
# What a refused command must leave as it was: .env, bin/update's state
# (receipts, journal; not update.lock, which records the last holder's pid),
# the backups, the checkout's files and the project's containers.
install_state() {
  python3 - "$CHECKOUT" "$BACKUPS" <<'PY'
import hashlib, pathlib, sys
checkout, backups = (pathlib.Path(arg) for arg in sys.argv[1:3])
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
print(".env", digest(checkout / ".env"))
for root in (checkout / ".futureagi-updates", backups):
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "update.lock"):
        print(path, digest(path))
PY
  git -C "$CHECKOUT" status --porcelain --untracked-files=all
  printf 'git diff HEAD: %s\n' "$(git -C "$CHECKOUT" diff HEAD | python3 -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest()[:16])')"
  project_containers
}
# The deployment files bin/update compares, one per line: the entries of the
# checkout's deploy/update/deployment-files.txt, read as bin/update reads them.
deployment_entries() {
  local entry
  while read -r entry; do
    entry=${entry%%#*}
    entry=${entry%"${entry##*[![:space:]]}"}
    [[ -z "$entry" ]] || printf '%s\n' "$entry"
  done <"$CHECKOUT/deploy/update/deployment-files.txt"
}

# Step 8 untags the release image so the stage has to pull it; KEEP_REF keeps
# its layers (no download) and the host gets the tag back on exit.
image_absent() { ! docker image inspect "$1" >/dev/null 2>&1; }
untag_release_image() {
  image_absent "$RELEASE_IMAGE" && return 0
  docker tag "$RELEASE_IMAGE" "$KEEP_REF" && docker image rm "$RELEASE_IMAGE"
}
restore_release_image() {
  image_absent "$KEEP_REF" && return 0
  if image_absent "$RELEASE_IMAGE"; then
    docker tag "$KEEP_REF" "$RELEASE_IMAGE" || return 1
  fi
  docker image rm "$KEEP_REF"
}

# ------------------------------------------------------------- cleanup --
cleanup_stack() { # final|previous: step 9, or removing a previous run's stack
  local when=${1:-final} leftovers=""
  if ((DOCKER_OK == 0)); then
    log "Docker is not reachable: nothing to clean up"
    return 0
  fi
  log "Removing project $PROJECT (containers, volumes, network): $when"
  if [[ -f "$CHECKOUT/docker-compose.yml" ]]; then
    (cd "$CHECKOUT" && "${DC[@]}" -p "$PROJECT" down -v --remove-orphans) >>"$RUN_DIR/90-down.log" 2>&1
  fi
  # Anything Compose did not know about (e.g. the checkout was removed).
  local ids
  ids=$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")
  # shellcheck disable=SC2086
  [[ -z "$ids" ]] || docker rm -f $ids >>"$RUN_DIR/90-down.log" 2>&1
  for volume in $(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT"); do
    docker volume rm "$volume" >>"$RUN_DIR/90-down.log" 2>&1
  done
  restore_release_image >>"$RUN_DIR/90-down.log" 2>&1
  leftovers=$(
    docker ps -a --format '{{.Names}}' | grep -E "^${PROJECT}([-_]|$)"
    docker volume ls --format '{{.Name}}' | grep -E "^${PROJECT}_"
    docker network ls --format '{{.Name}}' | grep -E "^${PROJECT}_"
    docker images --format '{{.Repository}}:{{.Tag}}' | grep -E "^${PROJECT}([-_:]|$)"
  )
  if [[ "$when" == previous ]]; then
    [[ -z "$leftovers" ]] || die "could not remove a previous rehearsal: $(tr '\n' ' ' <<<"$leftovers")"
  elif [[ -z "$leftovers" ]]; then
    record PASS 9 "no $PROJECT container, volume or network left"
  else
    record FAIL 9 "no $PROJECT container, volume or network left" "$(tr '\n' ' ' <<<"$leftovers")"
  fi
}
finish() {
  local code=$?
  trap - EXIT INT TERM
  if [[ "$MODE" != prepare ]]; then
    if [[ "${KEEP_STACK:-0}" == 1 ]]; then
      ((DOCKER_OK == 0)) || restore_release_image >>"$RUN_DIR/90-down.log" 2>&1
      log "KEEP_STACK=1: the stack keeps running. Remove it with: $(cleanup_command)"
    elif ((STACK_STARTED == 1)); then
      cleanup_stack
    fi
  fi
  if [[ -f "$RESULTS" ]]; then
    local took=$(($(date +%s) - STARTED_AT))
    {
      printf 'Rehearsal %s -> %s, project %s, %s (%s mode)\n' "$FROM_VERSION" "$TO_VERSION" "$PROJECT" "$STAMP" "$MODE"
      printf 'Docker Compose %s: %s\n' "${COMPOSE_VERSION:-(not probed)}" "${COMPOSE_SOURCE:-(none)}"
      printf 'Duration %dm%02ds\n' $((took / 60)) $((took % 60))
      printf 'PASS %s  FAIL %s\n\n' "$PASSES" "$FAILS"
      column -t -s $'\t' "$RESULTS" 2>/dev/null || cat "$RESULTS"
    } >"$RUN_DIR/summary.txt"
    cat "$RUN_DIR/summary.txt" >&2
    log "Logs: $RUN_DIR"
  fi
  rmdir "$BASE/.lock" 2>/dev/null
  if ((FAILS > 0)); then exit 1; fi
  exit "$code"
}

# ----------------------------------------------------------- preflight --
case "$PROJECT" in
  fagi-real-rehearsal | fagi-real-rehearsal-*) ;;
  *) echo "REHEARSAL_PROJECT must start with fagi-real-rehearsal, not $PROJECT" >&2; exit 2 ;;
esac
[[ "$BASE" == /* ]] || { echo "REHEARSAL_BASE must be absolute" >&2; exit 2; }
mkdir -p "$RUN_DIR" "$SECRETS" "$KEYS" "$BACKUPS" || exit 2
chmod 700 "$SECRETS" "$KEYS" "$BACKUPS"
if ! mkdir "$BASE/.lock" 2>/dev/null; then
  echo "another rehearsal holds $BASE/.lock (remove it if none runs)" >&2
  exit 2
fi
: >"$RESULTS"
trap finish EXIT
trap 'log "interrupted"; exit 130' INT TERM

for tool in git python3 curl tar; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is required"
done
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || die "python3 >= 3.9 is required"
[[ -z "$COMPOSE_BINARY" ]] || use_compose_binary
if docker info >/dev/null 2>&1 && compose_cmd; then
  DOCKER_OK=1
  COMPOSE_VERSION=$("${DC[@]}" version --short 2>/dev/null)
  if [[ -n "$COMPOSE_BINARY" ]]; then
    wanted=$("$COMPOSE_BINARY" version --short 2>/dev/null)
    [[ "${DC[*]}" == "docker compose" && -n "$wanted" && "$COMPOSE_VERSION" == "$wanted" ]] ||
      die "REHEARSAL_COMPOSE: \`${DC[*]} version\` reports ${COMPOSE_VERSION:-nothing}, not ${wanted:-a version} (see $RUN_DIR/compose-bin)"
  else
    COMPOSE_SOURCE="${DC[*]} ($(command -v "${DC[0]}"))"
  fi
  record INFO 1 "Docker Compose ${COMPOSE_VERSION:-(unknown version)}" "$COMPOSE_SOURCE"
  log "Docker: $(docker info --format '{{.Name}} {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes'); Compose: ${DC[*]} $COMPOSE_VERSION"
elif [[ "$MODE" != prepare ]]; then
  die "Docker (with Compose v2) is not reachable: $(docker info 2>&1 | tail -n 1)"
fi
if [[ "$MODE" == cleanup ]]; then
  STACK_STARTED=1
  exit 0
fi

# ------------------------------------------------------------- step 1 --
log "Step 1: copy the updater, clone, check out $FROM_VERSION, overlay the updater"
snapshot_updater "$UPDATER_SRC" "$RUN_DIR/updater" || die "cannot copy the updater from $UPDATER_SRC (is it mid-edit?)"
record INFO 1 "updater under test" "$UPDATER_SRC at $(git -C "$UPDATER_SRC" rev-parse --short HEAD 2>/dev/null || echo '?')$(
  [[ -n "$(git -C "$UPDATER_SRC" status --porcelain -- bin/update deploy/update scripts/generate_update_manifest.py 2>/dev/null)" ]] &&
    echo ' with uncommitted changes'), copy $(tree_hash "$RUN_DIR/updater") in $RUN_DIR/updater"
UPDATER_SRC="$RUN_DIR/updater"
if [[ -d "$CHECKOUT" ]]; then
  if ((DOCKER_OK == 1)) && [[ -n "$(project_containers)" ]]; then
    [[ "$MODE" == full ]] || die "a rehearsal stack ($PROJECT) still runs: remove it with $(cleanup_command)"
    cleanup_stack previous
  fi
  rm -rf "$CHECKOUT"
fi
if ! git clone --quiet "$SOURCE" "$CHECKOUT" 2>"$RUN_DIR/10-clone.log"; then
  [[ -n "$FALLBACK_SOURCE" ]] || die "git clone $SOURCE failed (set REHEARSAL_FALLBACK_SOURCE): $(cat "$RUN_DIR/10-clone.log")"
  rm -rf "$CHECKOUT"
  git clone --quiet "$FALLBACK_SOURCE" "$CHECKOUT" 2>>"$RUN_DIR/10-clone.log" || die "git clone failed"
fi
git -C "$CHECKOUT" checkout --quiet "$FROM_VERSION" || die "no tag $FROM_VERSION in $SOURCE"
git -C "$CHECKOUT" rev-parse --quiet --verify "refs/tags/$TO_VERSION" >/dev/null || die "no tag $TO_VERSION in $SOURCE"
overlay_updater "$CHECKOUT" || die "overlay failed"
check 1 "checkout at $FROM_VERSION with bin/update" "$(git -C "$CHECKOUT" describe --tags)" \
  test -x "$CHECKOUT/bin/update" -a -f "$CHECKOUT/deploy/update/__main__.py"
# The deployment files the two releases differ in: the check at FROM_VERSION
# (step 4) and the rollback (step 7) name them, as listed text and as the
# shell words of the `git checkout` commands they print.
DEPLOYMENT_ENTRIES=()
DRIFT_LISTED=""
DRIFT_WORDS=""
while IFS= read -r entry; do
  DEPLOYMENT_ENTRIES+=("$entry")
  git -C "$CHECKOUT" diff --quiet "$FROM_VERSION" "$TO_VERSION" -- "$entry" && continue
  DRIFT_LISTED+="${DRIFT_LISTED:+, }$entry"
  DRIFT_WORDS+="${DRIFT_WORDS:+ }$(py_quote "$entry")"
done < <(deployment_entries)
((${#DEPLOYMENT_ENTRIES[@]} > 0)) || die "$CHECKOUT/deploy/update/deployment-files.txt lists no files"
check 1 "$FROM_VERSION and $TO_VERSION differ in deployment files" "${DRIFT_LISTED:-none}" test -n "$DRIFT_LISTED"
# Commands printed for a shell outside the checkout start with this.
CD_CHECKOUT="cd $(py_quote "$(cd "$CHECKOUT" && pwd -P)") && "

# ------------------------------------------------------------- step 2 --
log "Step 2: .env and the real installer"
(umask 077 && cp "$CHECKOUT/.env.example" "$CHECKOUT/.env") || die "cannot create .env"
env_set "$CHECKOUT/.env" FUTURE_AGI_VERSION "$FROM_VERSION"
env_set "$CHECKOUT/.env" COMPOSE_PROJECT_NAME "$PROJECT"
if [[ -n "${REHEARSAL_READY_MAX_SECONDS:-}" ]]; then
  env_set "$CHECKOUT/.env" INSTALL_READY_MAX_SECONDS "$REHEARSAL_READY_MAX_SECONDS"
fi
while IFS='=' read -r var port; do
  env_set "$CHECKOUT/.env" "$var" "$port"
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    die "port $port ($var) is in use; set $var to a free port"
  fi
done <<<"$PORT_SETTINGS"
ADMIN_PASSWORD="Rh-$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')-9a!"
(umask 077 && printf '%s\n%s\n' "$ADMIN_EMAIL" "$ADMIN_PASSWORD" >"$SECRETS/admin-$STAMP")

if [[ "$MODE" == full ]]; then
  STACK_STARTED=1
  # Prefix assignments: the installer sees them, the command log does not.
  FAGI_ADMIN_EMAIL="$ADMIN_EMAIL" FAGI_ADMIN_NAME="$ADMIN_NAME" FAGI_ADMIN_PASSWORD="$ADMIN_PASSWORD" \
    run 20-install.log ./bin/install -y
  record INFO 2 "./bin/install -y duration" "${ELAPSED}s"
  check 2 "./bin/install -y exits 0" "exit $RC (20-install.log)" test "$RC" = 0
  if contains 20-install.log "legacy docker-compose v1"; then
    record INFO 2 "bin/install calls Compose $("${DC[@]}" version --short 2>/dev/null) 'legacy docker-compose v1'" "20-install.log"
  fi
  account_checks 2 "after install"
  FINGERPRINT_INSTALL="$FINGERPRINT"
  expect_running 2 "after install" "$FROM_VERSION" ""
  FROM_DIGEST=$(app_image_facts | cut -d'|' -f3 | grep -oE 'sha256:[0-9a-f]{64}' | head -n 1)
  record INFO 2 "$FROM_VERSION repo digest on this host" "${FROM_DIGEST:-none}"
  project_containers >"$RUN_DIR/21-containers-after-install.txt"
fi

# ------------------------------------------------------------- step 3 --
log "Step 3: keys, signed manifest for $TO_VERSION, update.conf, backup hook"
rm -rf "$RELEASE_SRC" "$RELEASE_DIR"
mkdir -p "$RELEASE_SRC" "$RELEASE_DIR"
git -C "$CHECKOUT" archive "$TO_VERSION" | tar -x -C "$RELEASE_SRC" || die "git archive $TO_VERSION failed"
mkdir -p "$RELEASE_SRC/bin"
overlay_updater "$RELEASE_SRC" || die "overlay of $RELEASE_SRC failed"

args=(--version "$TO_VERSION" --source-sha "$(git -C "$CHECKOUT" rev-parse "$TO_VERSION^{commit}")"
  --published-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --helm-chart "$HELM_CHART"
  --repo-root "$RELEASE_SRC" --output "$RELEASE_DIR/future-agi-update-manifest.json")
: >"$RUN_DIR/30-digests.txt"
while IFS='=' read -r component spec; do
  repo=${spec%%:*}
  suffix=""
  [[ "$spec" == *:* ]] && suffix=${spec#*:}
  ref="$repo:$TO_VERSION$suffix"
  digest=$(resolve_digest "$ref") || die "cannot resolve the digest of $ref"
  printf '%-18s %s@%s\n' "$component" "$ref" "$digest" >>"$RUN_DIR/30-digests.txt"
  args+=(--component "$component=docker.io/$ref@$digest")
  [[ "$component" == standalone ]] && SIGNED_DIGEST=$digest
done <<<"$COMPONENTS"
helm_digest=$(resolve_digest "ghcr.io/future-agi/charts/futureagi:${TO_VERSION#v}") || die "cannot resolve the Helm chart digest"
printf '%-18s %s@%s\n' helm "ghcr.io/future-agi/charts/futureagi:${TO_VERSION#v}" "$helm_digest" >>"$RUN_DIR/30-digests.txt"
args+=(--helm-digest "$helm_digest")
python3 "$UPDATER_SRC/scripts/generate_update_manifest.py" "${args[@]}" 2>"$RUN_DIR/31-generate.log"
check 3 "generate_update_manifest.py writes a valid manifest" "$RELEASE_DIR/future-agi-update-manifest.json" \
  test -s "$RELEASE_DIR/future-agi-update-manifest.json"

cat >"$HOOK" <<HOOK
#!/usr/bin/env bash
# Rehearsal backup hook (bin/update FUTURE_AGI_UPDATE_BACKUP_COMMAND): a
# pg_dump of the install's Postgres into a timestamped file; fails if empty.
set -euo pipefail
cd "\${FUTURE_AGI_UPDATE_ROOT:?run me from bin/update}"
if docker compose version >/dev/null 2>&1; then dc=(docker compose); else dc=(docker-compose); fi
# The user and database Compose gives Postgres (.env, else the compose defaults).
read -r pg_user pg_db < <("\${dc[@]}" config --format json | python3 -c 'import json, sys
e = json.load(sys.stdin)["services"]["postgres"]["environment"]
print(e["POSTGRES_USER"], e["POSTGRES_DB"])')
umask 077
out="$BACKUPS/pg-\${FUTURE_AGI_UPDATE_PROJECT}-\${FUTURE_AGI_UPDATE_FROM_VERSION}-to-\${FUTURE_AGI_UPDATE_TO_VERSION}-\$(date -u +%Y%m%dT%H%M%SZ).sql"
"\${dc[@]}" exec -T postgres pg_dump -U "\$pg_user" "\$pg_db" >"\$out.partial"
if [[ ! -s "\$out.partial" ]]; then
  rm -f "\$out.partial"
  echo "backup hook: pg_dump wrote nothing" >&2
  exit 1
fi
mv "\$out.partial" "\$out"
echo "backup hook: \$out (\$(wc -c <"\$out" | tr -d ' ') bytes)"
HOOK
chmod 0755 "$HOOK"
cat >"$CHECKOUT/update.conf" <<CONF
# Written by deploy/tests/update_e2e/rehearse_real_standalone.sh
FUTURE_AGI_UPDATE_MANIFEST_URL=$RELEASE_DIR/future-agi-update-manifest.json
FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=$KEYS/cosign.pub
FUTURE_AGI_UPDATE_COSIGN_OFFLINE=true
FUTURE_AGI_UPDATE_BACKUP_COMMAND=$HOOK
CONF

if [[ "$MODE" == prepare ]]; then
  record INFO 3 "prepare-only: manifest left unsigned, nothing started" "$RELEASE_DIR"
  exit 0
fi

if [[ ! -s "$KEYS/cosign.pub" || ! -s "$KEYS/cosign.key" || ! -s "$SECRETS/cosign-password" ]]; then
  rm -f "$KEYS/cosign.key" "$KEYS/cosign.pub"
  (umask 077 && python3 -c 'import secrets; print(secrets.token_urlsafe(24))' >"$SECRETS/cosign-password")
  docker run --rm --name "$PROJECT-cosign-keygen-$$" -e COSIGN_PASSWORD="$(cat "$SECRETS/cosign-password")" \
    -e HOME=/tmp --tmpfs /tmp --user "$(id -u):$(id -g)" -v "$KEYS:/keys" -w /keys \
    "$COSIGN_IMAGE" generate-key-pair >"$RUN_DIR/32-keygen.log" 2>&1
fi
check 3 "cosign key pair" "$KEYS/cosign.pub" test -s "$KEYS/cosign.pub"
docker run --rm --name "$PROJECT-cosign-sign-$$" -e COSIGN_PASSWORD="$(cat "$SECRETS/cosign-password")" \
  -e HOME=/tmp --tmpfs /tmp --user "$(id -u):$(id -g)" -v "$KEYS:/keys:ro" -v "$RELEASE_DIR:/w" \
  "$COSIGN_IMAGE" sign-blob --yes --key /keys/cosign.key --use-signing-config=false --tlog-upload=false \
  --bundle /w/future-agi-update-manifest.sigstore.json /w/future-agi-update-manifest.json \
  >"$RUN_DIR/33-sign.log" 2>&1
check 3 "manifest signed (bundle written)" "$RELEASE_DIR/future-agi-update-manifest.sigstore.json" \
  test -s "$RELEASE_DIR/future-agi-update-manifest.sigstore.json"

# ------------------------------------------------------------- step 4 --
log "Step 4: check and apply at $FROM_VERSION (deployment drift)"
run 40-check-drift.log ./bin/update check
check 4 "check exits 3 at $FROM_VERSION" "exit $RC" test "$RC" = 3
check 4 "check names the deployment drift" "40-check-drift.log" contains 40-check-drift.log "deployment files differ from $TO_VERSION"
check 4 "check names the drifted files" "$DRIFT_LISTED" contains 40-check-drift.log "differ from $TO_VERSION's ($DRIFT_LISTED)"
check 4 "check tells the user to git checkout $TO_VERSION" "40-check-drift.log" contains 40-check-drift.log "git checkout $TO_VERSION"
check 4 "check reports the signature verified" "40-check-drift.log" contains 40-check-drift.log "Signature     verified (public key"
record INFO 4 "check duration" "${ELAPSED}s"
backups_before=$(find "$BACKUPS" -type f | wc -l | tr -d ' ')
run 41-apply-drift.log ./bin/update apply
check 4 "apply exits 3 at $FROM_VERSION" "exit $RC" test "$RC" = 3
check 4 "apply refuses before the backup" "backup files: $backups_before" \
  test "$(find "$BACKUPS" -type f | wc -l | tr -d ' ')" = "$backups_before"
check 4 ".env still selects $FROM_VERSION" "" test "$(env_get "$CHECKOUT/.env" FUTURE_AGI_VERSION)" = "$FROM_VERSION"

# ------------------------------------------------------------- step 5 --
log "Step 5: git checkout $TO_VERSION, bin/install --no-up, check, apply"
cp -p "$CHECKOUT/.env" "$RUN_DIR/env.before-checkout"
project_containers >"$RUN_DIR/50-containers-before-noup.txt"
(cd "$CHECKOUT" && git checkout --quiet "$TO_VERSION") >"$RUN_DIR/50-git-checkout.log" 2>&1
check 5 "git checkout $TO_VERSION over the overlay" "50-git-checkout.log" test "$(git -C "$CHECKOUT" describe --tags)" = "$TO_VERSION"
overlay_updater "$CHECKOUT" || die "re-overlay failed"
git -C "$CHECKOUT" status --short >"$RUN_DIR/50-git-status.txt"

run 51-install-no-up.log ./bin/install --no-up -y
check 5 "./bin/install --no-up -y exits 0" "exit $RC" test "$RC" = 0
env_changes "$RUN_DIR/env.before-checkout" "$CHECKOUT/.env" >"$RUN_DIR/51-env-changes.txt"
record INFO 5 "bin/install --no-up changed .env" "$(tr '\n' ' ' <"$RUN_DIR/51-env-changes.txt")"
project_containers >"$RUN_DIR/51-containers-after-noup.txt"
check 5 "bin/install --no-up restarted nothing" "container ids and start times" \
  cmp -s "$RUN_DIR/50-containers-before-noup.txt" "$RUN_DIR/51-containers-after-noup.txt"

run 52-check.log ./bin/update check
check 5 "check exits 0" "exit $RC" test "$RC" = 0
check 5 "check: update available" "52-check.log" contains 52-check.log "update available: run ./bin/update apply"
check 5 "check: installed $FROM_VERSION" "52-check.log" contains 52-check.log "Installed     $FROM_VERSION"
check 5 "check: signature verified" "52-check.log" contains 52-check.log "Signature     verified (public key"
record INFO 5 "check duration" "${ELAPSED}s"
(cd "$CHECKOUT" && ./bin/update check --format json) </dev/null >"$RUN_DIR/52-check.json" 2>"$RUN_DIR/52-check.json.stderr"
python3 - "$RUN_DIR/52-check.json" "$TO_VERSION" >"$RUN_DIR/52-check-json.txt" 2>&1 <<'PY'
import json, sys
plan = json.load(open(sys.argv[1]))
assert plan["decision"] == "update_available", plan["decision"]
assert plan["env_changes"] == {"FUTURE_AGI_VERSION": sys.argv[2]}, plan["env_changes"]
assert not plan["reasons"] and not plan["deployment_drift"] and not plan["missing_env_keys"], plan
print("ok", plan["env_changes"], plan["notes"])
PY
check 5 "check --format json: only FUTURE_AGI_VERSION changes" "52-check-json.txt" grep -q '^ok' "$RUN_DIR/52-check-json.txt"

mkdir -p "$RELEASE_DIR/tampered"
python3 - "$RELEASE_DIR/future-agi-update-manifest.json" "$RELEASE_DIR/tampered/future-agi-update-manifest.json" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1]))
# Still a valid manifest, so only the signature can reject it.
manifest["migration"]["rollback"] = {"manual": "unsupported"}.get(manifest["migration"]["rollback"], "manual")
open(sys.argv[2], "w").write(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
PY
cp "$RELEASE_DIR/future-agi-update-manifest.sigstore.json" "$RELEASE_DIR/tampered/"
run 53-check-tampered.log ./bin/update check --manifest "$RELEASE_DIR/tampered/future-agi-update-manifest.json"
check 5 "a tampered manifest is refused (exit 3)" "exit $RC" test "$RC" = 3
check 5 "a tampered manifest reads NOT verified" "53-check-tampered.log" contains 53-check-tampered.log "NOT verified"

cp -p "$CHECKOUT/.env" "$RUN_DIR/env.before-apply"
run 54-apply-dry-run.log ./bin/update apply --dry-run
check 5 "apply --dry-run exits 0" "exit $RC" test "$RC" = 0
check 5 "apply --dry-run changes nothing" ".env" cmp -s "$RUN_DIR/env.before-apply" "$CHECKOUT/.env"

project_containers >"$RUN_DIR/55-containers-before-apply.txt"
backups_before=$(find "$BACKUPS" -type f | wc -l | tr -d ' ')
touch "$RUN_DIR/55-marker"
sleep 1
run 55-apply.log ./bin/update apply
check 5 "apply exits 0" "exit $RC (55-apply.log)" test "$RC" = 0
record INFO 5 "apply duration" "${ELAPSED}s"
project_containers >"$RUN_DIR/55-containers-after-apply.txt"
backup=$(find "$BACKUPS" -type f -name "pg-*-to-$TO_VERSION-*.sql" -newer "$RUN_DIR/55-marker" | head -n 1)
check 5 "the backup hook wrote a non-empty dump" "${backup:-none} ($([[ -n "$backup" ]] && wc -c <"$backup" | tr -d ' ') bytes)" test -s "${backup:-/nonexistent}"
check 5 "exactly one backup for this apply" "" test "$(find "$BACKUPS" -type f | wc -l | tr -d ' ')" = "$((backups_before + 1))"
receipt=$(find "$CHECKOUT/.futureagi-updates" -name 'receipt-*.json' -newer "$RUN_DIR/55-marker" | sort | tail -n 1)
[[ -n "$receipt" ]] && cp "$receipt" "$RUN_DIR/55-receipt.json"
check 5 "a receipt with status succeeded" "${receipt:-none}" grep -q '"status": "succeeded"' "${receipt:-/nonexistent}"
check 5 "the receipt records the signed digest" "$SIGNED_DIGEST" grep -qF "\"target_digest\": \"$SIGNED_DIGEST\"" "${receipt:-/nonexistent}"
receipt_leaks "$CHECKOUT/.env" "${receipt:-/nonexistent}" >"$RUN_DIR/55-receipt-secrets.txt" 2>&1
leak_rc=$?
check 5 "the receipt holds no .env secret" "$(cat "$RUN_DIR/55-receipt-secrets.txt")" test "$leak_rc" = 0
env_changes "$RUN_DIR/env.before-apply" "$CHECKOUT/.env" >"$RUN_DIR/55-env-changes.txt"
check 5 "apply changed only FUTURE_AGI_VERSION in .env" "$(tr '\n' ' ' <"$RUN_DIR/55-env-changes.txt")" \
  test "$(cat "$RUN_DIR/55-env-changes.txt")" = "$(printf -- '-FUTURE_AGI_VERSION=%s\n+FUTURE_AGI_VERSION=%s' "$FROM_VERSION" "$TO_VERSION")"
check 5 ".env keeps mode 0600" "$(env_mode "$CHECKOUT/.env")" test "$(env_mode "$CHECKOUT/.env")" = 600
expect_running 5 "after apply" "$TO_VERSION" "$SIGNED_DIGEST"
kept_containers 5 55-containers-before-apply.txt 55-containers-after-apply.txt
account_checks 5 "after apply"
check 5 "the account's data survived (API key fingerprint)" "${FINGERPRINT_INSTALL:-?} vs ${FINGERPRINT:-?}" \
  test -n "$FINGERPRINT" -a "$FINGERPRINT" = "${FINGERPRINT_INSTALL:-}"
run 56-check-up-to-date.log ./bin/update check
check 5 "check after apply exits 0" "exit $RC" test "$RC" = 0
check 5 "check after apply: up to date" "56-check-up-to-date.log" contains 56-check-up-to-date.log "up to date, nothing to do"
backups_before=$(find "$BACKUPS" -type f | wc -l | tr -d ' ')
run 57-apply-again.log ./bin/update apply
check 5 "apply again exits 0" "exit $RC" test "$RC" = 0
check 5 "apply again: nothing to do" "57-apply-again.log" contains 57-apply-again.log "Nothing to do"
check 5 "apply again runs no backup" "" test "$(find "$BACKUPS" -type f | wc -l | tr -d ' ')" = "$backups_before"

# An operator runs the installer again later. It pulls the pinned tag (the
# signed image) and runs `up -d`, which must find every container current:
# Standalone has no service to build, and the image id stays the same.
(cd "$CHECKOUT" && "${DC[@]}" config) </dev/null >"$RUN_DIR/58-compose-config.yml" 2>&1
check 5 "the Standalone stack has no service to build" "58-compose-config.yml" \
  test "$(grep -c '^services:' "$RUN_DIR/58-compose-config.yml")" = 1 -a "$(grep -cE '^ +build:' "$RUN_DIR/58-compose-config.yml")" = 0
cp -p "$CHECKOUT/.env" "$RUN_DIR/env.before-install-again"
project_containers >"$RUN_DIR/58-containers-before-install.txt"
image_before=$(app_image_facts | cut -d'|' -f1)
run 58-install-again.log ./bin/install -y
record INFO 5 "a later ./bin/install -y duration" "${ELAPSED}s"
check 5 "a later ./bin/install -y exits 0" "exit $RC (58-install-again.log)" test "$RC" = 0
project_containers >"$RUN_DIR/58-containers-after-install.txt"
diff "$RUN_DIR/58-containers-before-install.txt" "$RUN_DIR/58-containers-after-install.txt" >"$RUN_DIR/58-containers-diff.txt"
check 5 "a later ./bin/install starts nothing new" "same container ids, start times and images (58-containers-diff.txt: $(grep -c '^[<>]' "$RUN_DIR/58-containers-diff.txt") lines)" \
  test ! -s "$RUN_DIR/58-containers-diff.txt"
image_after=$(app_image_facts | cut -d'|' -f1)
check 5 "a later ./bin/install keeps the pinned image" "${image_before:-?} -> ${image_after:-?}" \
  test -n "$image_before" -a "$image_after" = "$image_before"
env_changes "$RUN_DIR/env.before-install-again" "$CHECKOUT/.env" >"$RUN_DIR/58-env-changes.txt"
record INFO 5 "a later ./bin/install changed .env" "$(tr '\n' ' ' <"$RUN_DIR/58-env-changes.txt")"
check 5 "a later ./bin/install keeps .env on $TO_VERSION" "" test "$(env_get "$CHECKOUT/.env" FUTURE_AGI_VERSION)" = "$TO_VERSION"
expect_running 5 "after a later ./bin/install" "$TO_VERSION" "$SIGNED_DIGEST"

# ------------------------------------------------------------- step 6 --
log "Step 6: status"
run 60-status.log ./bin/update status
check 6 "status exits 0" "exit $RC" test "$RC" = 0
check 6 "status shows the app running $TO_VERSION" "60-status.log" grep -qE "^  app .* running $TO_VERSION" "$RUN_DIR/60-status.log"
check 6 "status shows the last run" "60-status.log" contains 60-status.log "Last run      apply to $TO_VERSION: succeeded"

# ------------------------------------------------------------- step 7 --
# The rollback starts FROM_VERSION's images, which need FROM_VERSION's
# deployment files; the checkout (at TO_VERSION) has TO_VERSION's. Every
# rollback is refused, naming the commands that fix it, until those are run.
# rollback --yes runs from $BASE, outside the checkout, so the commands it
# prints start with `cd <checkout> && `: they are pasted in $BASE as printed.
log "Step 7: rollback to $FROM_VERSION (refused while the checkout has $TO_VERSION's deployment files)"
install_state >"$RUN_DIR/70-state-before.txt"
run 70-rollback-drift.log ./bin/update rollback
check 7 "rollback without --yes is refused (exit 3) while the deployment files differ" "exit $RC" test "$RC" = 3
check 7 "rollback without --yes names the files and their git checkout" "70-rollback-drift.log" \
  contains 70-rollback-drift.log "these differ in this checkout: $DRIFT_LISTED. Restore $FROM_VERSION's versions with \`git checkout $FROM_VERSION -- $DRIFT_WORDS\`"
run_in "$BASE" 71-rollback-yes-drift.log "$CHECKOUT/bin/update" rollback --yes
check 7 "rollback --yes (from outside the checkout) is refused (exit 3)" "exit $RC" test "$RC" = 3
check 7 "rollback --yes names the files that differ from $FROM_VERSION's" "71-rollback-yes-drift.log" \
  contains 71-rollback-yes-drift.log "need $FROM_VERSION's deployment files; these differ in this checkout: $DRIFT_LISTED."
install_state >"$RUN_DIR/71-state-after.txt"
diff "$RUN_DIR/70-state-before.txt" "$RUN_DIR/71-state-after.txt" >"$RUN_DIR/71-state-diff.txt"
check 7 "the refused rollbacks changed nothing" ".env, receipts, backups, git status and diff, containers (71-state-diff.txt)" \
  test ! -s "$RUN_DIR/71-state-diff.txt"
restore_command=$(printed_command 71-rollback-yes-drift.log "Restore $FROM_VERSION's versions with ")
rerun_command=$(printed_command 71-rollback-yes-drift.log "(bin/update and your env file stay as they are), then run ")
again_command=$(printed_command 71-rollback-yes-drift.log "Before updating again, run ")
check 7 "the refusal prints the git checkout that restores $FROM_VERSION's files" "${restore_command:-none}" \
  test "$restore_command" = "${CD_CHECKOUT}git checkout $FROM_VERSION -- $DRIFT_WORDS"
check 7 "the refusal prints the rollback to run again" "${rerun_command:-none}" \
  test "$rerun_command" = "${CD_CHECKOUT}./bin/update rollback --yes"
check 7 "the refusal prints the git checkout to run before updating again" "${again_command:-none}" \
  test "$again_command" = "${CD_CHECKOUT}git checkout $TO_VERSION -- $DRIFT_WORDS"

paste_command 72-paste-restore.log "$BASE" "$restore_command"
check 7 "the pasted restore command exits 0" "exit $RC (72-paste-restore.log)" test "$RC" = 0
check 7 "the checkout has $FROM_VERSION's deployment files" "git diff $FROM_VERSION -- <deployment files>" \
  git -C "$CHECKOUT" diff --quiet "$FROM_VERSION" -- "${DEPLOYMENT_ENTRIES[@]}"
run 73-rollback-no-yes.log ./bin/update rollback
check 7 "rollback without --yes exits 3" "exit $RC" test "$RC" = 3
check 7 "rollback without --yes asks for --yes" "73-rollback-no-yes.log" contains 73-rollback-no-yes.log "rerun with --yes"
cp -p "$CHECKOUT/.env" "$RUN_DIR/env.before-rollback"
paste_command 74-rollback.log "$BASE" "$rerun_command"
ROLLBACK_RC=$RC
record INFO 7 "rollback duration" "${ELAPSED}s"
check 7 "the pasted rollback --yes exits 0" "exit $RC (74-rollback.log)" test "$RC" = 0
env_changes "$RUN_DIR/env.before-rollback" "$CHECKOUT/.env" >"$RUN_DIR/74-env-changes.txt"
record INFO 7 "rollback changed .env" "$(tr '\n' ' ' <"$RUN_DIR/74-env-changes.txt")"
if ((ROLLBACK_RC == 0)); then
  check 7 "rollback changed only FUTURE_AGI_VERSION back" "$(tr '\n' ' ' <"$RUN_DIR/74-env-changes.txt")" \
    test "$(cat "$RUN_DIR/74-env-changes.txt")" = "$(printf -- '-FUTURE_AGI_VERSION=%s\n+FUTURE_AGI_VERSION=%s' "$TO_VERSION" "$FROM_VERSION")"
  expect_running 7 "after rollback" "$FROM_VERSION" "$FROM_DIGEST"
  account_checks 7 "after rollback"
  check 7 "the account's data survived the rollback" "${FINGERPRINT_INSTALL:-?} vs ${FINGERPRINT:-?}" \
    test -n "$FINGERPRINT" -a "$FINGERPRINT" = "${FINGERPRINT_INSTALL:-}"
  # A failed rollback is meant to be rerun, so only a finished one is refused.
  run 76-rollback-twice.log ./bin/update rollback --yes
  check 7 "a second rollback is refused (exit 3)" "exit $RC" test "$RC" = 3
  check 7 "a second rollback names the earlier one" "76-rollback-twice.log" contains 76-rollback-twice.log "already rolled back"
  # The checkout still has FROM_VERSION's files: an update is blocked on them until the printed command runs.
  run 77-check-before-restore.log ./bin/update check
  check 7 "check with $FROM_VERSION's files is blocked (exit 3)" "exit $RC" test "$RC" = 3
  check 7 "check names the git checkout that gives back $TO_VERSION's files" "77-check-before-restore.log" \
    contains 77-check-before-restore.log "git checkout $TO_VERSION -- $DRIFT_WORDS"
  paste_command 78-paste-before-updating.log "$BASE" "$again_command"
  check 7 "the pasted 'before updating again' command exits 0" "exit $RC (78-paste-before-updating.log)" test "$RC" = 0
  check 7 "the checkout has $TO_VERSION's deployment files again" "git status -- <deployment files>: clean" \
    test -z "$(git -C "$CHECKOUT" status --porcelain -- "${DEPLOYMENT_ENTRIES[@]}")"
else
  (cd "$CHECKOUT" && "${DC[@]}" ps -a && "${DC[@]}" logs --no-color --tail 400 app) >"$RUN_DIR/75-rollback-failure-logs.txt" 2>&1
  record INFO 7 "rollback failure logs" "75-rollback-failure-logs.txt"
  run 75-status-after-failed-rollback.log ./bin/update status
fi

# ------------------------------------------------------------- step 8 --
if ((ROLLBACK_RC == 0)); then
  log "Step 8: apply $TO_VERSION again in two steps: apply --stage, then apply"
  run 80-check.log ./bin/update check
  check 8 "check after rollback: update available" "exit $RC" contains 80-check.log "update available"

  # The stage must pull the release image: untag it (KEEP_REF keeps its layers).
  untag_release_image >"$RUN_DIR/81-untag.log" 2>&1
  check 8 "$RELEASE_IMAGE untagged before the stage" "layers kept as $KEEP_REF (81-untag.log)" image_absent "$RELEASE_IMAGE"
  cp -p "$CHECKOUT/.env" "$RUN_DIR/env.before-stage"
  project_containers >"$RUN_DIR/82-containers-before-stage.txt"
  backups_before=$(count_backups)
  touch "$RUN_DIR/82-marker"
  sleep 1
  run 82-apply-stage.log ./bin/update apply --stage
  check 8 "apply --stage exits 0" "exit $RC (82-apply-stage.log)" test "$RC" = 0
  record INFO 8 "apply --stage duration" "${ELAPSED}s"
  project_containers >"$RUN_DIR/82-containers-after-stage.txt"
  check 8 "apply --stage restarted nothing" "container ids, start times and images" \
    cmp -s "$RUN_DIR/82-containers-before-stage.txt" "$RUN_DIR/82-containers-after-stage.txt"
  expect_running 8 "after the stage" "$FROM_VERSION" "$FROM_DIGEST"
  check 8 "apply --stage reports $TO_VERSION staged, nothing restarted" "82-apply-stage.log" \
    grep -qE "Staged $TO_VERSION: .*Nothing was restarted" "$RUN_DIR/82-apply-stage.log"
  backup=$(find "$BACKUPS" -type f -name "pg-*-to-$TO_VERSION-*.sql" -newer "$RUN_DIR/82-marker" | head -n 1)
  check 8 "apply --stage ran the backup hook (non-empty dump)" "${backup:-none}" test -s "${backup:-/nonexistent}"
  check 8 "exactly one backup for the stage" "" test "$(count_backups)" = "$((backups_before + 1))"
  check 8 "apply --stage pulled $RELEASE_IMAGE" "82-apply-stage.log" contains 82-apply-stage.log "Pulling $RELEASE_IMAGE"
  pulled=$(docker image inspect --format '{{.Id}}|{{json .RepoDigests}}' "$RELEASE_IMAGE" 2>&1)
  check 8 "the pulled $RELEASE_IMAGE has the signed digest" "$pulled" grep -qF "futureagi/standalone@$SIGNED_DIGEST" <<<"$pulled"
  env_changes "$RUN_DIR/env.before-stage" "$CHECKOUT/.env" >"$RUN_DIR/82-env-changes.txt"
  check 8 "apply --stage changed only FUTURE_AGI_VERSION in .env" "$(tr '\n' ' ' <"$RUN_DIR/82-env-changes.txt")" \
    test "$(cat "$RUN_DIR/82-env-changes.txt")" = "$(printf -- '-FUTURE_AGI_VERSION=%s\n+FUTURE_AGI_VERSION=%s' "$FROM_VERSION" "$TO_VERSION")"
  staged=$(newest_receipt 82-marker)
  [[ -n "$staged" ]] && cp "$staged" "$RUN_DIR/82-receipt.json"
  check 8 "a receipt with status staged" "${staged:-none}" receipt_is "${staged:-/nonexistent}" 'r["status"] == "staged"'
  check 8 "the staged receipt records the signed digest and the verified image" "$SIGNED_DIGEST" \
    receipt_is "${staged:-/nonexistent}" \
    "[c['target_digest'] for c in r['components']] == ['$SIGNED_DIGEST'] and r['target_image_ids'].get('app') == '${pulled%%|*}'"
  receipt_leaks "$CHECKOUT/.env" "${staged:-/nonexistent}" >"$RUN_DIR/82-receipt-secrets.txt" 2>&1
  leak_rc=$?
  check 8 "the staged receipt holds no .env secret" "$(cat "$RUN_DIR/82-receipt-secrets.txt")" test "$leak_rc" = 0

  run 83-status-staged.log ./bin/update status
  check 8 "status shows the staged run" "83-status-staged.log" contains 83-status-staged.log "Last run      apply to $TO_VERSION: staged"
  run 84-check-staged.log ./bin/update check
  check 8 "check after the stage exits 0" "exit $RC" test "$RC" = 0
  check 8 "check after the stage: update available (not up to date)" "84-check-staged.log" \
    contains 84-check-staged.log "update available"
  record INFO 8 "check after the stage reads" "$(grep -E '^  (Installed|Decision) ' "$RUN_DIR/84-check-staged.log" | tr -s ' ' | tr '\n' ';')"

  cp -p "$CHECKOUT/.env" "$RUN_DIR/env.before-finish"
  project_containers >"$RUN_DIR/85-containers-before-apply.txt"
  backups_before=$(count_backups)
  touch "$RUN_DIR/85-marker"
  sleep 1
  run 85-apply-staged.log ./bin/update apply
  check 8 "apply after the stage exits 0" "exit $RC (85-apply-staged.log)" test "$RC" = 0
  record INFO 8 "apply after the stage duration" "${ELAPSED}s"
  project_containers >"$RUN_DIR/85-containers-after-apply.txt"
  check 8 "apply after the stage says it skips the backup" "85-apply-staged.log" contains 85-apply-staged.log "Skipping the backup"
  check 8 "apply after the stage runs no second backup" "backup files: $backups_before" test "$(count_backups)" = "$backups_before"
  receipt=$(newest_receipt 85-marker)
  [[ -n "$receipt" ]] && cp "$receipt" "$RUN_DIR/85-receipt.json"
  check 8 "a receipt with status succeeded" "${receipt:-none}" receipt_is "${receipt:-/nonexistent}" 'r["status"] == "succeeded"'
  check 8 "the receipt records the signed digest" "$SIGNED_DIGEST" grep -qF "\"target_digest\": \"$SIGNED_DIGEST\"" "${receipt:-/nonexistent}"
  check 8 "the receipt finishes the staged one, without a backup" "${staged:-none}" receipt_is "${receipt:-/nonexistent}" \
    "name(r.get('resumes')) == name(r['backup'].get('staged')) == '$(basename "${staged:-?}")' and not r['backup'].get('ran')"
  receipt_leaks "$CHECKOUT/.env" "${receipt:-/nonexistent}" >"$RUN_DIR/85-receipt-secrets.txt" 2>&1
  leak_rc=$?
  check 8 "the receipt holds no .env secret" "$(cat "$RUN_DIR/85-receipt-secrets.txt")" test "$leak_rc" = 0
  check 8 "apply after the stage left .env as staged" ".env" cmp -s "$RUN_DIR/env.before-finish" "$CHECKOUT/.env"
  check 8 ".env keeps mode 0600" "$(env_mode "$CHECKOUT/.env")" test "$(env_mode "$CHECKOUT/.env")" = 600
  expect_running 8 "after stage + apply" "$TO_VERSION" "$SIGNED_DIGEST"
  kept_containers 8 85-containers-before-apply.txt 85-containers-after-apply.txt
  account_checks 8 "after stage + apply"
  check 8 "the account's data survived the re-apply" "${FINGERPRINT_INSTALL:-?} vs ${FINGERPRINT:-?}" \
    test -n "$FINGERPRINT" -a "$FINGERPRINT" = "${FINGERPRINT_INSTALL:-}"
  run 86-check-up-to-date.log ./bin/update check
  check 8 "check after stage + apply: up to date" "exit $RC (86-check-up-to-date.log)" \
    contains 86-check-up-to-date.log "up to date, nothing to do"
else
  record INFO 8 "skipped: the rollback did not succeed" ""
fi
log "Step 9 runs on exit"
