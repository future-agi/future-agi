#!/usr/bin/env bash
# Legacy Streamable HTTP sessions, principal-bound and off-process.
#  1. A 2025-06-18 client initializes on gateway 1: the negotiated revision is
#     2025-06-18, the session's record is in Redis (mcps:{<org>}:<hash>)
#     with the session ttl, and the gateway logs the session's hash, never its
#     id.
#  2. The client calls a tool on its session; another key of the same org
#     presenting the session id gets 404, and the session keeps working.
#  3. The client's GET stream opens. [gate] After /__control/tools changes the
#     upstream's tools, a tools/list_changed with an event id arrives on it.
#  4. [gate] The client reconnects with Last-Event-ID and the event it missed
#     while away is replayed.
#  5. A second gateway process on the same Redis DB and state key serves the
#     same session: tools/list and a GET stream.
#  6. DELETE on gateway 2 ends the session (204): a tool call on gateway 1 is
#     then 404, a second DELETE is 404, the Redis record is gone, and neither
#     gateway's log holds the id.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<lane db> bash scripts/scenarios/68_m12_legacy_sessions.sh
#
# Steps 3 and 4's [gate] parts need the upstream client to pass an upstream
# list_changed on to the gateway (same wave), so they run only with
# SCN_WAVE_GATE=1, which the wave gate sets when it reruns this script on
# the merged tree; without it they print SKIP and the rest runs.
set -u
source "$(dirname "$0")/lib.sh"
source "$SCN_ROOT/lib_mcp.sh"

UPSTREAM_TOKEN=scn-test-mcp-upstream-token # gateway.yaml's bearer for primary
OTHER_KEY=scn-test-key-acme-other           # gateway.yaml's second key of org-acme
M12_GW2_PID=""
M12_GW2=""
M12_STREAM_PIDS=()
M12_KEYS=() # the session's Redis keys, deleted on exit
M12_IDS="" # m12_wait_events: the stream's event ids

[ -n "${SCN_REDIS:-}" ] || scn_fail m12.setup "SCN_REDIS is required: step 5 runs two gateways on one Redis"

# m12_redis <command> [args...]: one Redis command on SCN_REDIS, DB
# SCN_REDIS_DB; prints the reply (a list one item per line).
m12_redis() {
  python3 -I - "$SCN_REDIS" "${SCN_REDIS_DB:-0}" "$@" <<'PY'
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
        a = a.encode()
        out += b"$%d\r\n%s\r\n" % (len(a), a)
    sock.sendall(out)
    return reply()
cmd("SELECT", sys.argv[2])
r = cmd(*sys.argv[3:])
def show(v):
    if isinstance(v, list):
        for x in v:
            show(x)
    elif v is not None:
        print(v.decode())
show(r)
PY
}

# m12_session_key: the Redis key of the session's record, found by its hash
# whatever org the principal brings (none until principals are resolved).
m12_session_key() {
  local cursor=0 out key
  while :; do
    out=$(m12_redis SCAN "$cursor" MATCH "mcps:*:$M12_HASH" COUNT 1000)
    cursor=$(echo "$out" | head -1)
    key=$(echo "$out" | sed -n '2p')
    [ -n "$key" ] && { echo "$key"; return 0; }
    [ "$cursor" = 0 ] && return 0
  done
}

# m12_stream_open <gateway> <name> [last event id]: open a GET stream in the
# background into $SCN_DIR/<name>.{headers,body}; wait for its headers.
m12_stream_open() {
  local gw=$1 name=$2 last=${3:-} args deadline
  args=(-sS -N --max-time 120 -D "$SCN_DIR/$name.headers" -o "$SCN_DIR/$name.body"
    -H "Authorization: Bearer $SCN_KEY" -H "Mcp-Session-Id: $SCN_MCP_SESSION" -H 'Accept: text/event-stream')
  [ -n "$last" ] && args+=(-H "Last-Event-ID: $last")
  rm -f "$SCN_DIR/$name.headers" "$SCN_DIR/$name.body"
  curl "${args[@]}" "$gw/mcp" &
  M12_STREAM_PIDS+=($!)
  deadline=$(( $(date +%s) + 10 ))
  until [ -s "$SCN_DIR/$name.headers" ] && grep -q $'^\r$' "$SCN_DIR/$name.headers"; do
    [ "$(date +%s)" -ge "$deadline" ] && scn_fail "m12.$name" "no stream headers within 10s"
    sleep 0.1
  done
}

m12_stream_hdr() { # name header
  awk -v want="$(echo "$2" | tr '[:upper:]' '[:lower:]')" '
    { line = $0; sub(/\r$/, "", line); i = index(line, ":") }
    NR == 1 { split(line, p, " "); if (want == ":status") found = p[2] }
    i > 0 && tolower(substr(line, 1, i - 1)) == want { v = substr(line, i + 1); sub(/^ +/, "", v); found = v }
    END { print found }' "$SCN_DIR/$1.headers"
}

# m12_wait_events <name> <n>: wait until the stream has n events with an id,
# and set M12_IDS to their ids, one per line. Call it in the script's shell.
m12_wait_events() {
  local deadline=$(( $(date +%s) + 15 )) n
  while :; do
    n=$(grep -c '^id: ' "$SCN_DIR/$1.body" 2>/dev/null)
    [ "${n:-0}" -ge "$2" ] && break
    [ "$(date +%s)" -ge "$deadline" ] && scn_fail "m12.$1" "fewer than $2 events within 15s: $(cat "$SCN_DIR/$1.body" 2>/dev/null)"
    sleep 0.1
  done
  M12_IDS=$(sed -n 's/^id: //p' "$SCN_DIR/$1.body")
}

m12_stream_stop() {
  local pid
  for pid in ${M12_STREAM_PIDS[@]+"${M12_STREAM_PIDS[@]}"}; do kill "$pid" 2>/dev/null; done
  for pid in ${M12_STREAM_PIDS[@]+"${M12_STREAM_PIDS[@]}"}; do wait "$pid" 2>/dev/null; done
  M12_STREAM_PIDS=()
}

# m12_delete <gateway>: DELETE the session; prints the status.
m12_delete() {
  curl -sS -o "$SCN_DIR/delete.body" -w '%{http_code}' -X DELETE "$1/mcp" \
    -H "Authorization: Bearer $SCN_KEY" -H "Mcp-Session-Id: $SCN_MCP_SESSION"
}

# m12_gw2_up: a second gateway process with gateway 1's rendered config (same
# Redis DB, same state key) on another port.
m12_gw2_up() {
  local port unset_env=() var
  port=$(_scn_free_port)
  M12_GW2="http://127.0.0.1:$port"
  for var in $(env | sed -n 's/^\(AGENTCC_[A-Za-z0-9_]*\)=.*/\1/p'); do unset_env+=(-u "$var"); done
  env ${unset_env[@]+"${unset_env[@]}"} AGENTCC_PORT="$port" \
    "$SCN_BIN/agentcc" --config "$SCN_DIR/config.yaml" >"$SCN_DIR/gateway2.log" 2>&1 &
  M12_GW2_PID=$!
  _scn_wait_http gateway2 "$M12_GW2/healthz" 30 "$M12_GW2_PID"
}

m12_down() {
  m12_stream_stop
  if [ -n "$M12_GW2_PID" ]; then
    kill "$M12_GW2_PID" 2>/dev/null
    local i=0
    while kill -0 "$M12_GW2_PID" 2>/dev/null && [ $i -lt 50 ]; do sleep 0.2; i=$((i + 1)); done
    kill -9 "$M12_GW2_PID" 2>/dev/null
    M12_GW2_PID=""
  fi
  local key
  for key in ${M12_KEYS[@]+"${M12_KEYS[@]}"}; do m12_redis DEL "$key" >/dev/null 2>&1; done
  return 0
}

sha32() { python3 -I -c 'import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:32])' "$1"; }

scn_mcp_up primary --era dual --auth "bearer:$UPSTREAM_TOKEN"
scn_mcp_gateway_template "$SCN_ROOT/fixtures/M12/gateway.yaml"
SCN_CATALOG_WAIT_SKIP=all scn_up # no managed provider in the fixture
trap 'm12_down; _scn_mcp_down; scn_down' EXIT

scn_check "1. a 2025-06-18 client initializes; the record is in Redis; the id is never logged"
SCN_MCP_SESSION=""
code=$(scn_mcp_post /mcp '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"scn-mcp","version":"1"}}}')
scn_assert_eq 1.initialize_status 200 "$code"
scn_assert_eq 1.negotiated 2025-06-18 "$(scn_json 'd["result"]["protocolVersion"]')"
SCN_MCP_SESSION=$(scn_hdr Mcp-Session-Id)
scn_assert_eq 1.session_set yes "$([ -n "$SCN_MCP_SESSION" ] && echo yes)"
code=$(scn_mcp_post /mcp '{"jsonrpc":"2.0","method":"notifications/initialized"}')
scn_assert_eq 1.initialized_accepted 202 "$code"
M12_HASH=$(sha32 "$SCN_MCP_SESSION")
key=$(m12_session_key)
case "$key" in "mcps:{"*"}:$M12_HASH") M12_KEYS+=("$key" "mcpev:${key#mcps:}") ;; esac
scn_assert_eq 1.record_key_shape yes "$([ -n "$key" ] && [ "${key%:*}" != "$key" ] && echo yes)"
pttl=$(m12_redis PTTL "$key")
scn_assert_eq 1.record_ttl_within_session_ttl yes "$([ "$pttl" -gt 0 ] && [ "$pttl" -le 1800000 ] && echo yes)"
scn_assert_eq 1.record_holds_no_id 0 "$(m12_redis GET "$key" | grep -cF "$SCN_MCP_SESSION")"
scn_assert_eq 1.record_era 2025-06-18 "$(m12_redis GET "$key" | jq -r .era)"
scn_assert_eq 1.id_not_logged 0 "$(grep -cF "$SCN_MCP_SESSION" "$SCN_DIR/gateway.log")"
scn_assert_eq 1.hash_logged yes "$(grep -q "\"session_hash\":\"$M12_HASH\"" "$SCN_DIR/gateway.log" && echo yes)"

scn_check "2. a tool call on the session; another key of the org gets 404"
code=$(scn_mcp_post /mcp '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"primary_echo","arguments":{"text":"hello"}}}')
scn_assert_eq 2.call_status 200 "$code"
scn_assert_eq 2.call_text 'echo ok: {"text":"hello"}' "$(scn_json 'd["result"]["content"][0]["text"]')"
code=$(SCN_KEY=$OTHER_KEY scn_mcp_post /mcp '{"jsonrpc":"2.0","id":3,"method":"tools/list"}')
scn_assert_eq 2.other_key_404 404 "$code"
code=$(scn_mcp_post /mcp '{"jsonrpc":"2.0","id":4,"method":"tools/list"}')
scn_assert_eq 2.owner_still_served 200 "$code"

scn_check "3. the GET stream opens; [gate] a list_changed with an event id arrives on it"
m12_stream_open "$SCN_GW" stream1
scn_assert_eq 3.stream_status 200 "$(m12_stream_hdr stream1 :status)"
scn_assert_eq 3.stream_type text/event-stream "$(m12_stream_hdr stream1 Content-Type)"
if [ "${SCN_WAVE_GATE:-}" = 1 ]; then
  scn_mcp_control primary '[{"name":"only_tool"}]' tools >/dev/null || scn_fail 3.control "the mock refused /__control/tools"
  m12_wait_events stream1 1
  first_id=$(echo "$M12_IDS" | head -1)
  scn_assert_eq 3.list_changed_on_stream yes "$(grep -q 'notifications/tools/list_changed' "$SCN_DIR/stream1.body" && echo yes)"
  scn_assert_eq 3.event_id_set yes "$([ -n "$first_id" ] && echo yes)"
else
  echo "  SKIP 3.list_changed: left to the wave gate (SCN_WAVE_GATE=1)"
fi

scn_check "4. [gate] a reconnect with Last-Event-ID replays the missed event"
m12_stream_stop
if [ "${SCN_WAVE_GATE:-}" = 1 ]; then
  sleep 0.5
  scn_mcp_control primary '[{"name":"echo"}]' tools >/dev/null || scn_fail 4.control "the mock refused /__control/tools"
  m12_stream_open "$SCN_GW" stream2 "$first_id"
  m12_wait_events stream2 1
  replayed=$(echo "$M12_IDS" | head -1)
  scn_assert_eq 4.replayed_after_last yes "$([ -n "$replayed" ] && [ "$replayed" != "$first_id" ] && echo yes)"
  scn_assert_eq 4.replayed_is_list_changed yes "$(grep -q 'notifications/tools/list_changed' "$SCN_DIR/stream2.body" && echo yes)"
  m12_stream_stop
else
  echo "  SKIP 4.replay: left to the wave gate (SCN_WAVE_GATE=1)"
fi

scn_check "5. a second gateway process on the same Redis DB serves the session"
m12_gw2_up
code=$(SCN_GW=$M12_GW2 scn_mcp_post /mcp '{"jsonrpc":"2.0","id":5,"method":"tools/list"}')
scn_assert_eq 5.gw2_tools_list 200 "$code"
scn_assert_eq 5.gw2_lists_the_tools yes "$(scn_json 'any(t["name"].startswith("primary_") for t in d["result"]["tools"])' | grep -q true && echo yes)"
code=$(SCN_KEY=$OTHER_KEY SCN_GW=$M12_GW2 scn_mcp_post /mcp '{"jsonrpc":"2.0","id":6,"method":"tools/list"}')
scn_assert_eq 5.gw2_other_key_404 404 "$code"
m12_stream_open "$M12_GW2" stream3
scn_assert_eq 5.gw2_stream_status 200 "$(m12_stream_hdr stream3 :status)"

scn_check "6. DELETE on gateway 2 ends the session on both"
scn_assert_eq 6.delete 204 "$(m12_delete "$M12_GW2")"
deadline=$(( $(date +%s) + 10 ))
until ! kill -0 "${M12_STREAM_PIDS[0]}" 2>/dev/null; do
  [ "$(date +%s)" -ge "$deadline" ] && scn_fail 6.stream_ended "gateway 2's stream outlived DELETE"
  sleep 0.1
done
scn_assert_eq 6.stream_ended yes yes
M12_STREAM_PIDS=()
code=$(scn_mcp_post /mcp '{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"primary_echo","arguments":{"text":"again"}}}')
scn_assert_eq 6.gw1_call_after_delete 404 "$code"
scn_assert_eq 6.second_delete 404 "$(m12_delete "$SCN_GW")"
scn_assert_eq 6.record_gone 0 "$(m12_redis EXISTS "$key")"
scn_assert_eq 6.id_not_logged_gw1 0 "$(grep -cF "$SCN_MCP_SESSION" "$SCN_DIR/gateway.log")"
scn_assert_eq 6.id_not_logged_gw2 0 "$(grep -cF "$SCN_MCP_SESSION" "$SCN_DIR/gateway2.log")"

scn_pass
