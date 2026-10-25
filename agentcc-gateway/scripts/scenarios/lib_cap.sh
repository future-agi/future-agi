# shellcheck shell=bash
# Capability scenario library: agentcc-capmock (cmd/agentcc-capmock), the
# vendor stand-in for the gateway's capability packages. Source it after
# lib.sh:
#
#   source "$(dirname "$0")/lib.sh"
#   source "$(dirname "$0")/lib_cap.sh"
#   scn_cap_up                       # before scn_up: the gateway config needs $SCN_CAP
#   scn_cap_arm exa status:429 1     # the next exa call made through the gateway
#   code=$(scn_cap_call POST /exa/search '{"query":"q"}')   # or call the mock directly
#   scn_cap_received exa 1 | jq .
#
# The gateway forwards no test header upstream, so a scenario arms a fake's
# directives (status, delay_ms, fail_after, report_cost, job_delay,
# job_fail_at, url_expiry, drop_response; package capmock) for its next calls,
# as lib.sh's scn_post arms the mock upstream.
#
# The mock starts with an empty environment (PATH and HOME only): it refuses
# to start beside a variable named like a vendor key, and a scenario must
# never hand it one. It binds its own port (no free-port race), runs with
# --watch-pid $$ so it exits with the scenario even if no trap runs, and is
# also stopped by the EXIT trap, which this file keeps armed across scn_up.
#
# A gateway template may use $SCN_CAP (the mock's base URL; a fake is at
# $SCN_CAP/<fake>) once scn_cap_up has run.

[ -n "${_SCN_CAP_LOADED:-}" ] && return 0
_SCN_CAP_LOADED=1
command -v scn_up >/dev/null || { echo "lib_cap.sh: source lib.sh first" >&2; exit 1; }

SCN_CAPMOCK_BIN=$SCN_BIN/agentcc-capmock
SCN_CAP_DIR=$SCN_HOME/scn-cap/$(basename "$0" .sh) # mock log and address; scn_up wipes $SCN_DIR, not this
SCN_CAP_PID=""
export SCN_CAPMOCK_BIN

# scn_cap_build: build agentcc-capmock into $SCN_BIN once per script (or reuse
# it under SCN_SKIP_BUILD=1).
scn_cap_build() {
  [ -n "${_SCN_CAP_BUILT:-}" ] && return 0
  mkdir -p "$SCN_BIN" "$SCN_CAP_DIR"
  if [ -z "${SCN_SKIP_BUILD:-}" ] || [ ! -x "$SCN_CAPMOCK_BIN" ]; then
    (cd "$SCN_GATEWAY_DIR" && go build -o "$SCN_CAPMOCK_BIN" ./cmd/agentcc-capmock) >"$SCN_CAP_DIR/build.log" 2>&1 ||
      scn_fail scn_cap_build "build failed: $(tail -5 "$SCN_CAP_DIR/build.log")"
  fi
  _SCN_CAP_BUILT=1
}

# scn_cap_up: start the mock, wait for /__health, export SCN_CAP (its base
# URL) and SCN_CAP_PID.
scn_cap_up() {
  local deadline
  [ -n "$SCN_CAP_PID" ] && scn_fail scn_cap_up "the capability mock is already running"
  scn_cap_build
  rm -f "$SCN_CAP_DIR/capmock.addr"
  env -i PATH="$PATH" HOME="$HOME" "$SCN_CAPMOCK_BIN" --addr 127.0.0.1:0 \
    --addr-file "$SCN_CAP_DIR/capmock.addr" --watch-pid $$ >"$SCN_CAP_DIR/capmock.log" 2>&1 &
  SCN_CAP_PID=$!
  _scn_cap_arm_trap
  deadline=$(( $(date +%s) + 15 ))
  until [ -s "$SCN_CAP_DIR/capmock.addr" ] && curl -sf -o /dev/null "http://$(cat "$SCN_CAP_DIR/capmock.addr")/__health"; do
    kill -0 "$SCN_CAP_PID" 2>/dev/null || scn_fail scn_cap_up "the mock exited during startup: $(tail -5 "$SCN_CAP_DIR/capmock.log")"
    [ "$(date +%s)" -ge "$deadline" ] && scn_fail scn_cap_up "the mock is not ready: $(tail -5 "$SCN_CAP_DIR/capmock.log")"
    sleep 0.1
  done
  SCN_CAP="http://$(cat "$SCN_CAP_DIR/capmock.addr")"
  export SCN_CAP SCN_CAP_PID
  echo "scn_cap_up: capability mock on $SCN_CAP (pid $SCN_CAP_PID, log $SCN_CAP_DIR/capmock.log)"
}

# _scn_cap_down: stop the mock (SIGTERM, then SIGKILL after 10 s).
_scn_cap_down() {
  local n=0
  [ -n "$SCN_CAP_PID" ] || return 0
  kill "$SCN_CAP_PID" 2>/dev/null || { SCN_CAP_PID=""; return 0; }
  while kill -0 "$SCN_CAP_PID" 2>/dev/null && [ $n -lt 50 ]; do sleep 0.2; n=$((n + 1)); done
  kill -9 "$SCN_CAP_PID" 2>/dev/null
  SCN_CAP_PID=""
  return 0
}

# _scn_cap_arm_trap: put _scn_cap_down in front of the current EXIT trap.
_scn_cap_arm_trap() {
  local current
  current=$(trap -p EXIT | sed -e "s/^trap -- '//" -e "s/' EXIT\$//")
  case "$current" in
    *_scn_cap_down*) ;;
    "") builtin trap '_scn_cap_down' EXIT ;;
    *) builtin trap "_scn_cap_down; $current" EXIT ;;
  esac
}

# scn_up replaces the EXIT trap with its own (scn_down); keep the mock's stop
# in front of whatever it, or another library's wrapper, armed.
eval "_scn_cap_prev_scn_up() $(declare -f scn_up | sed '1d')"
scn_up() {
  local status
  _scn_cap_prev_scn_up "$@"
  status=$?
  [ -n "$SCN_CAP_PID" ] && _scn_cap_arm_trap
  return $status
}

# scn_cap_arm <fake> <directives> [calls]: directives for the fake's next
# calls that carry no x-capmock-behavior header (calls 0 or omitted: until
# scn_cap_clear).
scn_cap_arm() {
  local body code
  body=$(python3 -I -c 'import json,sys; print(json.dumps({"fake": sys.argv[1], "behavior": sys.argv[2], "calls": int(sys.argv[3])}))' \
    "$1" "$2" "${3:-0}")
  code=$(curl -sS -o "$SCN_CAP_DIR/arm.body" -w '%{http_code}' -X POST "$SCN_CAP/__arm" --data-binary "$body")
  [ "$code" = 200 ] || scn_fail "scn_cap_arm $1" "arming $2 returned HTTP $code: $(cat "$SCN_CAP_DIR/arm.body")"
}

# scn_cap_clear [fake]: clear one fake's arming, or every fake's.
scn_cap_clear() { curl -sS -o /dev/null -X DELETE "$SCN_CAP/__arm${1:+?fake=$1}"; }

# scn_cap_reset: forget calls, arming, jobs, files and sessions.
scn_cap_reset() { curl -sS -o /dev/null -X POST "$SCN_CAP/__reset"; }

# scn_cap_received <fake> [n]: the fake's last n requests (JSON; all when n
# is 0 or omitted).
scn_cap_received() { curl -sS "$SCN_CAP/__received?fake=$1&last=${2:-0}"; }

# scn_cap_call <method> <path> [body] [curl args...]: call the mock directly;
# prints the HTTP status and keeps the response in $SCN_CAP_DIR/last.{headers,body}.
scn_cap_call() {
  local method=$1 path=$2 body=${3:-} args
  shift 2
  [ $# -gt 0 ] && shift
  args=(-sS -o "$SCN_CAP_DIR/last.body" -D "$SCN_CAP_DIR/last.headers" -w '%{http_code}' -X "$method")
  [ -n "$body" ] && args+=(-H 'Content-Type: application/json' --data-binary "$body")
  # A failed transfer (drop_response) still prints its %{http_code}, 000.
  curl "${args[@]}" "$@" "$SCN_CAP$path"
  return 0
}

# scn_cap_hdr <name>: a header of the last scn_cap_call response.
scn_cap_hdr() {
  awk -v want="$(echo "$1" | tr '[:upper:]' '[:lower:]')" '
    { line = $0; sub(/\r$/, "", line); i = index(line, ":") }
    i > 0 && tolower(substr(line, 1, i - 1)) == want { v = substr(line, i + 1); sub(/^ +/, "", v); found = v }
    END { print found }' "$SCN_CAP_DIR/last.headers"
}

# scn_cap_golden <golden.json>: send each exchange of a golden file to the
# mock and assert its status, headers and JSON fields. An exchange is
# {name, request: {method, path, headers?, body?}, expect: {status,
# headers?: [names], json?: {dotted.path: type | {"equals": value}}}}; a
# type is string, number, boolean, array, object, null or present.
scn_cap_golden() {
  local out
  out=$(python3 -I - "$1" "$SCN_CAP" <<'PY'
import json, sys, urllib.request, urllib.error
golden, base = json.load(open(sys.argv[1])), sys.argv[2]
TYPES = {"string": str, "number": (int, float), "boolean": bool, "array": list, "object": dict}
def get(doc, path):
    for part in path.split("."):
        if isinstance(doc, list):
            doc = doc[int(part)]
        elif isinstance(doc, dict) and part in doc:
            doc = doc[part]
        else:
            raise KeyError(path)
    return doc
failures, checks = [], 0
for ex in golden:
    req, want = ex["request"], ex["expect"]
    body = req.get("body")
    data = None if body is None else (body if isinstance(body, str) else json.dumps(body)).encode()
    r = urllib.request.Request(base + req["path"], data=data, method=req["method"], headers=req.get("headers", {}))
    try:
        res = urllib.request.urlopen(r, timeout=10)
        status, headers, raw = res.status, res.headers, res.read()
    except urllib.error.HTTPError as e:
        status, headers, raw = e.code, e.headers, e.read()
    checks += 1
    if status != want["status"]:
        failures.append("%s: status %d, want %d (%s)" % (ex["name"], status, want["status"], raw[:200]))
        continue
    for h in want.get("headers", []):
        checks += 1
        if not headers.get(h):
            failures.append("%s: no %s header" % (ex["name"], h))
    if "json" in want:
        try:
            doc = json.loads(raw)
        except ValueError:
            failures.append("%s: body is not JSON: %r" % (ex["name"], raw[:200]))
            continue
        for path, rule in want["json"].items():
            checks += 1
            try:
                v = get(doc, path)
            except (KeyError, IndexError, ValueError):
                failures.append("%s: no %s" % (ex["name"], path))
                continue
            if isinstance(rule, dict):
                ok = v == rule["equals"]
            elif rule == "present":
                ok = True
            elif rule == "null":
                ok = v is None
            else:
                ok = isinstance(v, TYPES[rule]) and not (rule == "number" and isinstance(v, bool))
            if not ok:
                failures.append("%s: %s is %r, want %s" % (ex["name"], path, v, rule))
print(json.dumps({"exchanges": len(golden), "checks": checks, "failures": failures}))
PY
  ) || scn_fail "scn_cap_golden $1" "the checker failed: $out"
  echo "$out"
}
