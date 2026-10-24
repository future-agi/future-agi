# shellcheck shell=bash
# Real-binary scenario library: the agentcc gateway against the agentcc-mockup
# upstream, driven with curl. Source it from a scenario script:
#
#   source "$(dirname "$0")/lib.sh"
#   scn_up
#   scn_check "1. something"
#   code=$(scn_chat '{"model":"mock/ok-1","messages":[{"role":"user","content":"hi"}]}')
#   scn_assert_eq 1.status 200 "$code"
#   scn_pass
#
# Environment:
#   SCN_REDIS, SCN_REDIS_DB  Redis for the gateway (e.g. 127.0.0.1:16379 and a
#                            DB of your own). Unset: the gateway keeps state in memory
#                            (run_all.sh requires both, with a non-zero DB).
#   SCN_OTEL_EXPORTER        "stdout" turns on OTel with the stdout exporter, so
#                            spans land in scn_gateway_log.
#   SCN_CONFIG_TEMPLATE      a gateway config template to use in place of
#                            config/base.yaml (same placeholders; see base.yaml).
#   SCN_MOCK_FIXTURES        the mock's fixtures dir (default fixtures/common).
#   SCN_CATALOG_WAIT_SKIP    managed provider ids (comma-separated) that scn_up's
#                            catalog wait ignores, or "all" to skip the wait.
#   SCN_RUN, SCN_HOME       run id and scratch dir (default: the run id from
#                            the git branch, under ${TMPDIR:-/tmp}/agentcc-scenarios).
#   SCN_SKIP_BUILD=1         reuse the binaries already built under SCN_HOME.
#   SCN_REDIS_KEEP=1         skip deleting earlier runs' Redis keys.
#   SCN_SELFTEST_FLIP=<name> flip the expected value of the named assertion.
#
# Rules: call scn_assert_* and scn_fail in the script's own shell, not inside
# $(...), since they exit the script. Requests go through scn_chat / scn_stream
# / scn_post so their response lands in $SCN_DIR/last.{headers,body}. The
# gateway does not forward x-mock-behavior upstream; scn_post arms the mock
# with any x-mock-behavior header it is given, for the duration of the call
# (replacing, then clearing, a behaviour armed with scn_behavior).

SCN_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SCN_GATEWAY_DIR=$(cd "$SCN_ROOT/../.." && pwd)
# 32+ characters with no placeholder word: the gateway refuses to start on an
# empty, short, default or placeholder admin token (internal/config/admin_token.go).
SCN_ADMIN_TOKEN=scn-admin-4f8e2b7d9c1a6e3f5b0d8a2c7e9f1b3d
SCN_ORG=${SCN_ORG:-org-acme}
SCN_KEY=${SCN_KEY:-scn-test-key-acme}
SCN_ASSERTIONS=0

_scn_run() {
  if [ -n "${SCN_RUN:-}" ]; then echo "$SCN_RUN"; return; fi
  local branch
  branch=$(git -C "$SCN_GATEWAY_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo local)
  case "$branch" in
    gw/*|gw-ee/*) echo "${branch#*/}" ;;
    *) echo "$branch" | tr '/' '-' ;;
  esac
}
SCN_RUN=$(_scn_run)
SCN_HOME=${SCN_HOME:-${TMPDIR:-/tmp}/agentcc-scenarios/$SCN_RUN}
SCN_BIN=$SCN_HOME/scn-bin
SCN_DIR=$SCN_HOME/scn-run/$(basename "$0" .sh)
SCN_NAME=$(basename "$0")
SCN_ORGS_FILE=$SCN_HOME/scn-orgs-db${SCN_REDIS_DB:-0} # orgs whose Redis keys a run must delete

# --- failure and assertions ------------------------------------------------

# scn_fail <name> <message>: record the failed assertion and stop the script.
scn_fail() {
  local name=$1; shift
  echo "SCN FAIL [$name]: $*" >&2
  if [ -n "${SCN_FAIL_FILE:-}" ] && [ ! -s "$SCN_FAIL_FILE" ]; then
    echo "$name" >"$SCN_FAIL_FILE"
  fi
  exit 1
}

_scn_expected() { # name expected: the expected value, flipped for the self-test
  if [ "${SCN_SELFTEST_FLIP:-}" = "$1" ]; then echo "$2<flipped-by-selftest>"; else echo "$2"; fi
}

# scn_assert_eq <name> <expected> <actual>
scn_assert_eq() {
  local expected
  expected=$(_scn_expected "$1" "$2")
  SCN_ASSERTIONS=$((SCN_ASSERTIONS + 1))
  if [ "$expected" != "$3" ]; then
    scn_fail "$1" "expected '$expected', got '$3'"
  fi
  echo "  ok  $1 = $3"
}

# scn_assert_jq <name> <jq filter> <expected> [file]: compare `jq -r <filter>`
# over file (default: the last response body).
scn_assert_jq() {
  local file=${4:-$SCN_DIR/last.body} actual
  actual=$(jq -r "$2" <"$file" 2>&1) || scn_fail "$1" "jq '$2' failed: $actual"
  scn_assert_eq "$1" "$3" "$actual"
}

# scn_check <title>: print a section header.
scn_check() { echo; echo "=== $*"; }

# scn_pass: the last line of a scenario that got to its end.
scn_pass() { echo; echo "SCN PASS $SCN_NAME ($SCN_ASSERTIONS assertions)"; }

# --- reading responses -----------------------------------------------------

# scn_hdr <name>: a header of the last response (case-insensitive).
scn_hdr() {
  awk -v want="$(echo "$1" | tr '[:upper:]' '[:lower:]')" '
    { line = $0; sub(/\r$/, "", line); i = index(line, ":") }
    i > 0 && tolower(substr(line, 1, i - 1)) == want { v = substr(line, i + 1); sub(/^ +/, "", v); found = v }
    END { print found }' "$SCN_DIR/last.headers"
}

# scn_json <python expr> [file]: evaluate expr with d bound to the parsed file
# (default: the last response body). A server-sent-event body parses to the
# list of its data payloads, "[DONE]" included. Strings print raw, the rest
# as JSON.
scn_json() {
  python3 -I - "$1" "${2:-$SCN_DIR/last.body}" <<'PY'
import json, sys
expr, path = sys.argv[1], sys.argv[2]
raw = open(path, encoding="utf-8").read()
try:
    d = json.loads(raw)
except ValueError:
    d = []
    for line in raw.splitlines():
        if line.startswith("data: "):
            payload = line[len("data: "):]
            d.append(payload if payload == "[DONE]" else json.loads(payload))
v = eval(expr, {"d": d, "json": json})
print(v if isinstance(v, str) else json.dumps(v))
PY
}

# --- requests --------------------------------------------------------------

# scn_post <path> <json body> [curl args...]: POST to the gateway with the
# Bearer $SCN_KEY; prints the HTTP status, keeps headers and body in $SCN_DIR.
scn_post() {
  local path=$1 body=$2 behavior="" arg prev="" code
  shift 2
  for arg in "$@"; do
    if [ "$prev" = "-H" ] || [ "$prev" = "--header" ]; then
      case "$(echo "$arg" | tr '[:upper:]' '[:lower:]')" in
        x-mock-behavior:*) behavior=$(echo "${arg#*:}" | sed 's/^ *//') ;;
      esac
    fi
    prev=$arg
  done
  [ -n "$behavior" ] && scn_behavior "$behavior" >/dev/null
  code=$(curl -sS -o "$SCN_DIR/last.body" -D "$SCN_DIR/last.headers" -w '%{http_code}' \
    -X POST "$SCN_GW$path" -H "Authorization: Bearer $SCN_KEY" -H 'Content-Type: application/json' \
    --data-binary "$body" "$@")
  [ -n "$behavior" ] && scn_behavior_clear >/dev/null
  echo "$code"
}

scn_chat() { scn_post /v1/chat/completions "$@"; }

# scn_stream <json body> [curl args...]: scn_chat with "stream": true.
scn_stream() {
  local body
  body=$(python3 -I -c 'import json,sys; b=json.loads(sys.argv[1]); b["stream"]=True; print(json.dumps(b))' "$1")
  shift
  scn_post /v1/chat/completions "$body" -N "$@"
}

# scn_admin <method> <path> [json]: an admin call; prints the HTTP status and
# keeps the body in $SCN_DIR/last.body.
scn_admin() {
  local args=(-sS -o "$SCN_DIR/last.body" -D "$SCN_DIR/last.headers" -w '%{http_code}' -X "$1" "$SCN_GW$2"
    -H "Authorization: Bearer $SCN_ADMIN_TOKEN")
  [ $# -ge 3 ] && args+=(-H 'Content-Type: application/json' --data-binary "$3")
  curl "${args[@]}"
}

_scn_admin_ok() { # name method path [json]: an admin call that must succeed
  local name=$1 code
  shift
  code=$(scn_admin "$@")
  case "$code" in
    2??) ;;
    *) scn_fail "$name" "$1 $2 returned HTTP $code: $(cat "$SCN_DIR/last.body")" ;;
  esac
}

# scn_org_config <org> <json>: replace the org's config.
scn_org_config() {
  _scn_track_org "$1"
  _scn_admin_ok "scn_org_config $1" PUT "/-/orgs/$1/config" "$2"
}

# scn_key <org> [json]: create a key for the org and print it. json may add
# models, providers or metadata.
scn_key() {
  _scn_track_org "$1"
  local req
  req=$(python3 -I -c '
import json, sys
org, extra = sys.argv[1], json.loads(sys.argv[2] or "{}")
meta = dict(extra.pop("metadata", {}), org_id=org)
print(json.dumps(dict({"name": "scn-" + org}, **extra, metadata=meta)))' "$1" "${2:-}")
  _scn_admin_ok "scn_key $1" POST /-/keys "$req"
  jq -r .key <"$SCN_DIR/last.body"
}

# scn_grant <org> <usd>: grant usd more managed credit. It pushes the
# cumulative total with a version one higher than the last
# (PUT /-/orgs/{id}/credits/granted), and uses POST /-/orgs/{id}/credits
# when the gateway answers that route with 404 or 405.
scn_grant() {
  local org=$1 usd=$2 vfile="$SCN_DIR/grant-version-$1" tfile="$SCN_DIR/grant-total-$1" version total code
  _scn_track_org "$org"
  version=$(( $(cat "$vfile" 2>/dev/null || echo 0) + 1 ))
  total=$(python3 -I -c 'import sys; from decimal import Decimal, ROUND_HALF_UP
print(int(Decimal(sys.argv[1]) + (Decimal(sys.argv[2]) * 1000000).quantize(Decimal(1), ROUND_HALF_UP)))' \
    "$(cat "$tfile" 2>/dev/null || echo 0)" "$usd")
  code=$(scn_admin PUT "/-/orgs/$org/credits/granted" \
    "{\"granted_total_micros\": $total, \"version\": $version, \"settled_spend_floor_micros\": 0}")
  case "$code" in
    404|405) _scn_admin_ok "scn_grant $org" POST "/-/orgs/$org/credits" "{\"amount_usd\": $usd}" ;;
    2??)
      [ "$(jq -r .applied <"$SCN_DIR/last.body")" = false ] &&
        scn_fail "scn_grant $org" "grant version $version not applied: $(cat "$SCN_DIR/last.body")"
      echo "$version" >"$vfile" ;;
    *) scn_fail "scn_grant $org" "PUT credits/granted returned HTTP $code: $(cat "$SCN_DIR/last.body")" ;;
  esac
  echo "$total" >"$tfile"
}

# --- the mock --------------------------------------------------------------

# scn_behavior <x-mock-behavior> [model]: arm the mock for inference calls that
# carry no header of their own (only for model, if given).
scn_behavior() {
  local body
  body=$(python3 -I -c 'import json,sys; print(json.dumps({"behavior": sys.argv[1], "model": sys.argv[2]}))' "$1" "${2:-}")
  curl -sS -X POST "$SCN_MOCK/__behavior" --data-binary "$body"
}
scn_behavior_clear() { curl -sS -X DELETE "$SCN_MOCK/__behavior"; }

# scn_received [n] [path filter]: the last n requests the mock was sent (JSON).
scn_received() { curl -sS "$SCN_MOCK/__received?last=${1:-0}&path=${2:-}"; }

# scn_charges: the mock's cumulative upstream charge, in micros.
scn_charges() { curl -sS "$SCN_MOCK/__charges" | jq -r .total_micros; }

# scn_logs [min entries] [timeout s]: the request logs the gateway delivered to
# the mock (JSON), waiting for at least min entries (default 1). The gateway
# flushes every 5 s.
scn_logs() {
  local want=${1:-1} deadline=$(( $(date +%s) + ${2:-20} )) body
  while :; do
    body=$(curl -sS "$SCN_MOCK/__logs")
    [ "$(echo "$body" | jq '.logs | length')" -ge "$want" ] && { echo "$body"; return 0; }
    [ "$(date +%s)" -ge "$deadline" ] && { echo "$body"; return 1; }
    sleep 0.5
  done
}

# scn_gateway_log: the gateway's stdout and stderr so far.
scn_gateway_log() { cat "$SCN_DIR/gateway.log"; }

# --- lifecycle -------------------------------------------------------------

_scn_free_port() {
  python3 -I -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

_scn_wait_http() { # name url timeout_s pid: wait for a 2xx; $SCN_DIR/<name>.log explains a failure
  local name=$1 url=$2 deadline=$(( $(date +%s) + $3 )) pid=$4
  while :; do
    curl -sf -o /dev/null "$url" && return 0
    kill -0 "$pid" 2>/dev/null || scn_fail scn_up "$name exited during startup: $(tail -5 "$SCN_DIR/$name.log")"
    [ "$(date +%s)" -ge "$deadline" ] && scn_fail scn_up "$name not ready at $url: $(tail -5 "$SCN_DIR/$name.log")"
    sleep 0.2
  done
}

# _scn_track_org <org>: remember the org so the next run deletes its Redis keys.
_scn_track_org() {
  [ -n "${SCN_REDIS:-}" ] || return 0
  grep -qxF "$1" "$SCN_ORGS_FILE" 2>/dev/null || echo "$1" >>"$SCN_ORGS_FILE"
}

# _scn_redis_cleanup: delete this run's gateway prefix and the keys of every
# org a scenario touched in this DB (SCAN + DEL; never FLUSHDB).
_scn_redis_cleanup() {
  [ -n "${SCN_REDIS:-}" ] && [ -z "${SCN_REDIS_KEEP:-}" ] || return 0
  local args=("scn:$SCN_RUN:*") org
  while IFS= read -r org; do
    [ -n "$org" ] || continue
    args+=("usage:$org:*" "quota:$org:*" "pause:$org:*" "*{$org}*" "srem|mc:needs_push|$org" "srem|mc:outbox_orgs|$org")
  done <"$SCN_ORGS_FILE"
  python3 -I - "$SCN_REDIS" "${SCN_REDIS_DB:-0}" "${args[@]}" <<'PY' || scn_fail scn_up "redis cleanup failed"
import socket, sys
host, port = sys.argv[1].rsplit(":", 1)
sock = socket.create_connection((host, int(port)), timeout=5)
rd = sock.makefile("rb")
def reply():
    line = rd.readline()
    kind, rest = line[:1], line[1:-2]
    if kind == b"-":
        raise SystemExit("redis: " + rest.decode())
    if kind in (b"+", b":"):
        return rest
    if kind == b"$":
        n = int(rest)
        return None if n < 0 else rd.read(n + 2)[:-2]
    return [reply() for _ in range(int(rest))]
def cmd(*args):
    out = b"*%d\r\n" % len(args)
    for a in args:
        a = a if isinstance(a, bytes) else str(a).encode()
        out += b"$%d\r\n%s\r\n" % (len(a), a)
    sock.sendall(out)
    return reply()
cmd("SELECT", sys.argv[2])
for arg in sys.argv[3:]:
    if arg.startswith("srem|"):
        _, key, member = arg.split("|", 2)
        cmd("SREM", key, member)
        continue
    cursor = b"0"
    while True:
        cursor, keys = cmd("SCAN", cursor, "MATCH", arg, "COUNT", 1000)
        if keys:
            cmd("DEL", *keys)
        if cursor == b"0":
            break
PY
}

_scn_render_config() { # template out
  python3 -I - "$1" "$2" <<'PY' || scn_fail scn_up "cannot render the gateway config from $1"
import os, re, sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
values = {k: os.environ["SCN_T_" + k] for k in
          ("GATEWAY_PORT", "GATEWAY_URL", "MOCK_URL", "ADMIN_TOKEN", "SCN_DIR", "SCN_ROOT", "RUN")}
for k, v in values.items():
    text = text.replace("@@%s@@" % k, v)
live = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
left = sorted(set(re.findall(r"@@[A-Z_]+@@", live)))
if left:
    sys.exit("unknown placeholders: %s" % ", ".join(left))
def appended(key, block):
    global text
    if re.search(r"^%s:" % key, text, re.M):
        sys.exit("the template defines `%s:` itself; scn_up appends it" % key)
    text = text.rstrip("\n") + "\n" + block
if os.environ.get("SCN_T_REDIS"):
    appended("redis", 'redis:\n  enabled: true\n  address: "%s"\n  db: %s\n  prefix: "scn:%s:"\n'
             % (os.environ["SCN_T_REDIS"], os.environ["SCN_T_REDIS_DB"], values["RUN"]))
if os.environ.get("SCN_T_OTEL"):
    appended("otel", 'otel:\n  enabled: true\n  exporter: "%s"\n  service_name: "agentcc-scenario"\n'
             % os.environ["SCN_T_OTEL"])
open(dst, "w", encoding="utf-8").write(text)
PY
}

# scn_up: build the gateway and the mock into the run's scratch dir, start the
# mock and then the gateway on free ports, and wait until the gateway is
# healthy and its managed catalogs come from the mock. Exports SCN_GW,
# SCN_MOCK and SCN_DIR; scn_down runs on exit.
scn_up() {
  local tool
  for tool in go curl jq python3; do
    command -v "$tool" >/dev/null || scn_fail scn_up "$tool is required"
  done
  rm -rf "$SCN_DIR"
  mkdir -p "$SCN_DIR" "$SCN_BIN"
  touch "$SCN_ORGS_FILE"
  if [ -z "${SCN_SKIP_BUILD:-}" ] || [ ! -x "$SCN_BIN/agentcc" ] || [ ! -x "$SCN_BIN/agentcc-mockup" ]; then
    (cd "$SCN_GATEWAY_DIR" && go build -o "$SCN_BIN/agentcc" ./cmd/agentcc &&
      go build -o "$SCN_BIN/agentcc-mockup" ./cmd/agentcc-mockup) >"$SCN_DIR/build.log" 2>&1 ||
      scn_fail scn_up "build failed: $(tail -5 "$SCN_DIR/build.log")"
  fi
  trap scn_down EXIT
  trap 'exit 130' INT TERM

  local mock_port gw_port
  mock_port=$(_scn_free_port)
  gw_port=$(_scn_free_port)
  SCN_MOCK="http://127.0.0.1:$mock_port"
  SCN_GW="http://127.0.0.1:$gw_port"
  "$SCN_BIN/agentcc-mockup" --addr "127.0.0.1:$mock_port" \
    --fixtures "${SCN_MOCK_FIXTURES:-$SCN_ROOT/fixtures/common}" >"$SCN_DIR/mock.log" 2>&1 &
  SCN_MOCK_PID=$!
  _scn_wait_http mock "$SCN_MOCK/__health" 15 "$SCN_MOCK_PID"

  _scn_redis_cleanup
  _scn_track_org "$SCN_ORG"
  SCN_T_GATEWAY_PORT=$gw_port SCN_T_GATEWAY_URL=$SCN_GW SCN_T_MOCK_URL=$SCN_MOCK \
    SCN_T_ADMIN_TOKEN=$SCN_ADMIN_TOKEN SCN_T_SCN_DIR=$SCN_DIR SCN_T_SCN_ROOT=$SCN_ROOT SCN_T_RUN=$SCN_RUN \
    SCN_T_REDIS=${SCN_REDIS:-} SCN_T_REDIS_DB=${SCN_REDIS_DB:-0} SCN_T_OTEL=${SCN_OTEL_EXPORTER:-} \
    _scn_render_config "${SCN_CONFIG_TEMPLATE:-$SCN_ROOT/config/base.yaml}" "$SCN_DIR/config.yaml"

  # Only the rendered config and AGENTCC_PORT configure the gateway: AGENTCC_*
  # variables from the caller's shell would override it silently.
  local unset_env=() var
  for var in $(env | sed -n 's/^\(AGENTCC_[A-Za-z0-9_]*\)=.*/\1/p'); do unset_env+=(-u "$var"); done
  env ${unset_env[@]+"${unset_env[@]}"} AGENTCC_PORT="$gw_port" \
    "$SCN_BIN/agentcc" --config "$SCN_DIR/config.yaml" >"$SCN_DIR/gateway.log" 2>&1 &
  SCN_GW_PID=$!
  _scn_wait_http gateway "$SCN_GW/healthz" 30 "$SCN_GW_PID"
  _scn_wait_catalog
  export SCN_GW SCN_MOCK SCN_DIR
  echo "scn_up: gateway $SCN_GW, mock $SCN_MOCK, logs in $SCN_DIR"
}

# _scn_wait_catalog: wait until every managed provider's catalog is the mock's
# (the gateway starts on its bundled snapshot and fetches on its first tick).
# SCN_CATALOG_WAIT_SKIP=<id>[,<id>...] names providers the wait ignores, for a
# source that never reports live (a price-card template, or a base URL that
# is down); SCN_CATALOG_WAIT_SKIP=all skips the wait. Unset, every managed
# provider must report live within 30 s. The wait counts the providers still
# pending: one with a missing or empty id can never be skipped, so it holds
# the wait and is named "<no provider id>" when the wait fails.
_scn_wait_catalog() {
  local skip=${SCN_CATALOG_WAIT_SKIP:-} deadline=$(( $(date +%s) + 30 )) code pending count
  [ "$skip" = all ] && return 0
  while :; do
    pending=""
    code=$(scn_admin GET /-/catalog)
    [ "$code" = 404 ] && return 0 # no managed providers in this config
    if [ "$code" = 200 ] && pending=$(jq -r --arg skip "$skip" '
        ($skip | split(",") | map(gsub("^ +| +$"; "")) | map(select(. != ""))) as $skipped
        | [.providers[] | select(.source != "live")
           | select(.provider as $id | $skipped | any(.[]; . == $id) | not)]
        | "\(length) \(map(.provider // "" | tostring | if . == "" then "<no provider id>" else . end) | join(","))"' \
        <"$SCN_DIR/last.body"); then
      count=${pending%% *}
      pending=${pending#* }
      [ "$count" = 0 ] && return 0
    else
      pending=""
    fi
    [ "$(date +%s)" -ge "$deadline" ] && scn_fail scn_up "managed catalog never loaded from the mock${pending:+ for $pending}" \
      "(SCN_CATALOG_WAIT_SKIP=<id> skips a provider that never reports live): $(cat "$SCN_DIR/last.body")"
    sleep 0.2
  done
}

# scn_down: stop the gateway and the mock, and delete this run's Redis keys.
scn_down() {
  local pid
  for pid in ${SCN_GW_PID:-} ${SCN_MOCK_PID:-}; do
    kill "$pid" 2>/dev/null || continue
    local i=0
    while kill -0 "$pid" 2>/dev/null && [ $i -lt 50 ]; do sleep 0.2; i=$((i + 1)); done
    kill -9 "$pid" 2>/dev/null
  done
  SCN_GW_PID="" SCN_MOCK_PID=""
  _scn_redis_cleanup
  return 0
}
