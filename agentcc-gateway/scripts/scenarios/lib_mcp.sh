# shellcheck shell=bash
# MCP scenario library: agentcc-mcpmock upstreams (cmd/agentcc-mcpmock) in
# front of the real gateway. Source it after lib.sh:
#
#   source "$(dirname "$0")/lib.sh"
#   source "$(dirname "$0")/lib_mcp.sh"
#   scn_mcp_up github --era 2025-06-18 --auth bearer:scn-test-token
#   scn_mcp_gateway_template "$SCN_ROOT/fixtures/mcp/mcpmock-gateway.yaml"
#   scn_up
#   scn_mcp_session_init
#   code=$(scn_mcp_post /mcp '{"jsonrpc":"2.0","id":2,"method":"tools/list"}')
#   scn_mcp_received github 1 tools/call | jq .
#
# Mocks start before scn_up, on a port the mock binds itself (no free-port
# race), and are stopped on exit. lib.sh's scn_up sets its own EXIT trap
# (scn_down), so sourcing this file wraps scn_up to re-arm the trap after it:
# stop the mocks, then scn_down. Before scn_up the trap stops the mocks only.
#
# A gateway template may use, besides base.yaml's placeholders:
#   @@MCP_<NAME>_URL@@  the base URL of the mock started as <name> (the gateway
#                       posts to <url>/mcp, the mock's default path)
#   @@MCPMOCK_BIN@@     the built agentcc-mcpmock (for a stdio server's command)
#   @@MCP_FIXTURES@@    the default fixtures, fixtures/mcp/common
# and it gets `state_keys` (SCN_MCP_STATE_KEY) under its `mcp:` block.
#
# Environment:
#   SCN_MCP_STATE_KEY   the fixed test state key (an opaque string
#                       of at least 32 bytes; AGENTCC_MCP_STATE_KEYS is a comma
#                       list of them). A fake value for local tests only.
#   SCN_MCP_POST_TIMEOUT  seconds one scn_mcp_post may take (default 30), so a
#                       stream that never ends fails the step instead of
#                       hanging the run. The mock's control calls take 10.
#
# The mock keeps credential headers as presence and SHA-256 in /__received.
# For an upstream the gateway reaches with api_key auth in a custom header,
# pass `--auth-header <name>` to scn_mcp_up so that header is reported as
# auth; `--credential-header <name>` adds a name the built-in rule misses.

[ -n "${_SCN_MCP_LOADED:-}" ] && return 0
_SCN_MCP_LOADED=1
command -v scn_up >/dev/null || { echo "lib_mcp.sh: source lib.sh first" >&2; exit 1; }

SCN_MCP_STATE_KEY=${SCN_MCP_STATE_KEY:-scn-mcp-test-state-key-0123456789abcdef0123456789}
SCN_MCP_FIXTURES=$SCN_ROOT/fixtures/mcp/common
SCN_MCPMOCK_BIN=$SCN_BIN/agentcc-mcpmock
SCN_MCP_DIR=$SCN_HOME/scn-mcp/$(basename "$0" .sh) # mock logs; scn_up wipes $SCN_DIR, not this
export SCN_MCP_STATE_KEY SCN_MCP_FIXTURES SCN_MCPMOCK_BIN
_SCN_MCP_NAMES=()
_SCN_MCP_PIDS=()

# _scn_mcp_var <name>: the upper-case variable stem for a mock name.
_scn_mcp_var() { echo "$1" | tr '[:lower:]' '[:upper:]' | tr -c 'A-Z0-9\n' '_'; }

# scn_mcp_build: build agentcc-mcpmock into $SCN_BIN once per script (or reuse
# it under SCN_SKIP_BUILD=1) and export SCN_MCPMOCK_BIN.
scn_mcp_build() {
  [ -n "${_SCN_MCP_BUILT:-}" ] && return 0
  mkdir -p "$SCN_BIN" "$SCN_MCP_DIR"
  if [ -z "${SCN_SKIP_BUILD:-}" ] || [ ! -x "$SCN_MCPMOCK_BIN" ]; then
    (cd "$SCN_GATEWAY_DIR" && go build -o "$SCN_MCPMOCK_BIN" ./cmd/agentcc-mcpmock) >"$SCN_MCP_DIR/build.log" 2>&1 ||
      scn_fail scn_mcp_build "build failed: $(tail -5 "$SCN_MCP_DIR/build.log")"
  fi
  _SCN_MCP_BUILT=1
  export SCN_MCPMOCK_BIN
}

# scn_mcp_up [name] [flags...]: start an HTTP mock named name (default
# "main") with agentcc-mcpmock's flags (default --era dual and the common
# fixtures), wait for /__health, and export SCN_MCP_<NAME>_URL (its base URL),
# SCN_MCP_<NAME>_PORT and SCN_MCP_<NAME>_PID. Call it before scn_up.
scn_mcp_up() {
  local name=${1:-main} var port pid arg has_era="" has_fix="" deadline
  [ $# -gt 0 ] && shift
  var=$(_scn_mcp_var "$name")
  for arg in "$@"; do
    case "$arg" in --era|--era=*) has_era=1 ;; --fixtures|--fixtures=*) has_fix=1 ;; esac
  done
  for arg in ${_SCN_MCP_NAMES[@]+"${_SCN_MCP_NAMES[@]}"}; do
    [ "$(_scn_mcp_var "$arg")" = "$var" ] && scn_fail scn_mcp_up "a mock named $name was already started"
  done
  [ ${#_SCN_MCP_NAMES[@]} -eq 0 ] && rm -rf "$SCN_MCP_DIR" # this script's earlier run
  scn_mcp_build
  local defaults=()
  [ -n "$has_era" ] || defaults+=(--era dual)
  [ -n "$has_fix" ] || defaults+=(--fixtures "$SCN_MCP_FIXTURES")
  rm -f "$SCN_MCP_DIR/$name.addr"
  "$SCN_MCPMOCK_BIN" --mode http --addr 127.0.0.1:0 --addr-file "$SCN_MCP_DIR/$name.addr" --name "mcpmock-$name" \
    ${defaults[@]+"${defaults[@]}"} "$@" >"$SCN_MCP_DIR/$name.log" 2>&1 &
  pid=$!
  _SCN_MCP_NAMES+=("$name")
  _SCN_MCP_PIDS+=("$pid")
  [ -n "${SCN_GW_PID:-}" ] || trap _scn_mcp_down EXIT
  deadline=$(( $(date +%s) + 15 ))
  until [ -s "$SCN_MCP_DIR/$name.addr" ] && curl -sf -o /dev/null "http://$(cat "$SCN_MCP_DIR/$name.addr")/__health"; do
    kill -0 "$pid" 2>/dev/null || scn_fail scn_mcp_up "mock $name exited during startup: $(tail -5 "$SCN_MCP_DIR/$name.log")"
    [ "$(date +%s)" -ge "$deadline" ] && scn_fail scn_mcp_up "mock $name not ready: $(tail -5 "$SCN_MCP_DIR/$name.log")"
    sleep 0.1
  done
  port=$(sed 's/.*://' "$SCN_MCP_DIR/$name.addr")
  export "SCN_MCP_${var}_URL=http://127.0.0.1:$port" "SCN_MCP_${var}_PORT=$port" "SCN_MCP_${var}_PID=$pid"
  echo "scn_mcp_up: mock $name on http://127.0.0.1:$port (pid $pid, log $SCN_MCP_DIR/$name.log)"
}

# _scn_mcp_down: stop every mock (SIGTERM, then SIGKILL after 10 s).
_scn_mcp_down() {
  local i pid n
  for i in "${!_SCN_MCP_PIDS[@]}"; do
    pid=${_SCN_MCP_PIDS[$i]}
    kill "$pid" 2>/dev/null || continue
    n=0
    while kill -0 "$pid" 2>/dev/null && [ $n -lt 50 ]; do sleep 0.2; n=$((n + 1)); done
    kill -9 "$pid" 2>/dev/null
  done
  _SCN_MCP_PIDS=()
  return 0
}

# lib.sh's scn_up, kept under another name. It arms `trap scn_down EXIT`
# before it starts the mock upstream and the gateway, so a failure inside it would
# exit with only scn_down armed and leave the MCP mocks running. The wrapper
# widens that one trap as it is set, and re-arms it after scn_up returns:
# the mocks stop first, then scn_down stops the gateway and the mock upstream.
eval "_scn_mcp_prev_scn_up() $(declare -f scn_up | sed '1d')"
scn_up() {
  local status
  # shellcheck disable=SC2317,SC2064 # called by lib.sh's scn_up; "$@" is the caller's trap
  trap() {
    if [ $# -eq 2 ] && [ "$1" = scn_down ] && [ "$2" = EXIT ]; then
      builtin trap '_scn_mcp_down; scn_down' EXIT
    else
      builtin trap "$@"
    fi
  }
  _scn_mcp_prev_scn_up "$@"
  status=$?
  unset -f trap
  builtin trap '_scn_mcp_down; scn_down' EXIT
  return $status
}

# scn_mcp_gateway_template <fixture.yaml>: render the MCP placeholders into
# $SCN_BIN/mcp-template.yaml (scn_up wipes $SCN_DIR), add the test state key
# under the top-level `mcp:` block, set SCN_CONFIG_TEMPLATE, and export
# AGENTCC_MCP_STATE_KEYS. scn_up unsets every AGENTCC_* variable before it
# starts the gateway, so the key reaches the gateway through the config.
scn_mcp_gateway_template() {
  local src=$1 out=$SCN_BIN/mcp-template.yaml
  [ -f "$src" ] || scn_fail scn_mcp_gateway_template "no template $src"
  scn_mcp_build
  python3 -I - "$src" "$out" <<'PY' || scn_fail scn_mcp_gateway_template "cannot render $src"
import os, re, sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
def url(m):
    v = os.environ.get("SCN_MCP_%s_URL" % m.group(1))
    if not v:
        sys.exit("%s: no mock named %s is running (scn_mcp_up %s first)" % (m.group(0), m.group(1).lower(), m.group(1).lower()))
    return v
text = re.sub(r"@@MCP_([A-Z0-9_]+?)_URL@@", url, text)
text = text.replace("@@MCPMOCK_BIN@@", os.environ["SCN_MCPMOCK_BIN"])
text = text.replace("@@MCP_FIXTURES@@", os.environ["SCN_MCP_FIXTURES"])
lines = text.split("\n")
heads = [i for i, l in enumerate(lines) if re.match(r"mcp:\s*(#.*)?$", l)]
if len(heads) == 1:
    i = heads[0]
    body = [l for l in lines[i + 1:] if l.strip() and not l.lstrip().startswith("#")]
    first = body[0] if body else ""
    indent = re.match(r"\s*", first).group(0)
    if not indent:
        sys.exit("the template's mcp: block is empty")
    block = []
    for l in lines[i + 1:]:
        if l.strip() and not l.startswith(" ") and not l.lstrip().startswith("#"):
            break
        block.append(l)
    if not any(re.match(r"%sstate_keys:" % indent, l) for l in block):
        lines.insert(i + 1, '%sstate_keys: ["%s"] # lib_mcp.sh: the fixed test key' % (indent, os.environ["SCN_MCP_STATE_KEY"]))
elif heads:
    sys.exit("the template has %d top-level mcp: blocks" % len(heads))
open(dst, "w", encoding="utf-8").write("\n".join(lines))
PY
  export AGENTCC_MCP_STATE_KEYS=$SCN_MCP_STATE_KEY SCN_CONFIG_TEMPLATE=$out
}

# scn_mcp_post <path> <json> [header...]: POST a JSON-RPC message to the
# gateway with the Bearer $SCN_KEY and Accept for JSON and SSE, plus
# Mcp-Session-Id when scn_mcp_session_init has set SCN_MCP_SESSION. Each extra
# argument is one "Name: value" header. Prints the HTTP status; the response
# lands in $SCN_DIR/last.{headers,body}. A reply still streaming after
# SCN_MCP_POST_TIMEOUT seconds prints "timeout" (not the status of a cut-off
# stream) and returns 28.
scn_mcp_post() {
  local path=$1 body=$2 h args=() code rc
  shift 2
  args=(-sS --max-time "${SCN_MCP_POST_TIMEOUT:-30}" -o "$SCN_DIR/last.body" -D "$SCN_DIR/last.headers"
    -w '%{http_code}' -X POST "$SCN_GW$path"
    -H "Authorization: Bearer $SCN_KEY" -H 'Content-Type: application/json'
    -H 'Accept: application/json, text/event-stream')
  [ -n "${SCN_MCP_SESSION:-}" ] && args+=(-H "Mcp-Session-Id: $SCN_MCP_SESSION")
  for h in "$@"; do args+=(-H "$h"); done
  code=$(curl "${args[@]}" --data-binary "$body")
  rc=$?
  if [ $rc -eq 28 ]; then
    echo "scn_mcp_post: $path did not finish within ${SCN_MCP_POST_TIMEOUT:-30}s (HTTP $code so far)" >&2
    echo timeout
    return 28
  fi
  echo "$code"
  return $rc
}

# scn_mcp_session_init [protocol version] [path]: open a legacy MCP session on
# the gateway (initialize, then notifications/initialized) and set
# SCN_MCP_SESSION. Default 2025-06-18 on /mcp. Call it in the script's shell.
scn_mcp_session_init() {
  local version=${1:-2025-06-18} path=${2:-/mcp} code
  SCN_MCP_SESSION=""
  code=$(scn_mcp_post "$path" "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"$version\",\"capabilities\":{},\"clientInfo\":{\"name\":\"scn-mcp\",\"version\":\"1\"}}}")
  [ "$code" = 200 ] || scn_fail scn_mcp_session_init "initialize returned HTTP $code: $(cat "$SCN_DIR/last.body")"
  SCN_MCP_SESSION=$(scn_hdr Mcp-Session-Id)
  [ -n "$SCN_MCP_SESSION" ] || scn_fail scn_mcp_session_init "initialize set no Mcp-Session-Id: $(cat "$SCN_DIR/last.headers")"
  export SCN_MCP_SESSION
  code=$(scn_mcp_post "$path" '{"jsonrpc":"2.0","method":"notifications/initialized"}')
  case "$code" in 2??) ;; *) scn_fail scn_mcp_session_init "notifications/initialized returned HTTP $code" ;; esac
}

_scn_mcp_url() { # name: the mock's base URL, or fail
  local url
  url=$(eval "echo \${SCN_MCP_$(_scn_mcp_var "$1")_URL:-}")
  [ -n "$url" ] || { echo "lib_mcp.sh: no mock named $1" >&2; return 1; }
  echo "$url"
}

# scn_mcp_received <name> [last] [method]: the mock's /__received (JSON):
# the last n messages (all when 0 or absent), of one JSON-RPC method if given.
scn_mcp_received() {
  local url
  url=$(_scn_mcp_url "$1") || return 1
  curl -sS --max-time 10 "$url/__received?last=${2:-0}&method=${3:-}"
}

# scn_mcp_charges <name>: the mock's own cost log, /__charges (JSON).
scn_mcp_charges() {
  local url
  url=$(_scn_mcp_url "$1") || return 1
  curl -sS --max-time 10 "$url/__charges"
}

# scn_mcp_control <name> <json> [behavior|tools|reset|oauth/token]: POST to
# the mock's /__control/<what> (default behavior). Prints the reply; returns
# non-zero unless the mock answered 2xx.
scn_mcp_control() {
  local url out code
  url=$(_scn_mcp_url "$1") || return 1
  out=$(curl -sS --max-time 10 -w '\n%{http_code}' -X POST "$url/__control/${3:-behavior}" -H 'Content-Type: application/json' \
    --data-binary "$2")
  code=${out##*$'\n'}
  echo "${out%$'\n'*}"
  case "$code" in 2??) return 0 ;; *) return 1 ;; esac
}
