#!/usr/bin/env bash
# The upstream MCP client behind the real gateway, on agentcc-mcpmock.
#  1. pages: a dual-era mock at /rpc/v2 (not /mcp), answering every POST in
#     SSE, with 120 tools in pages of 50. The gateway lists all 120; the mock
#     saw every request on /rpc/v2 only, three tools/list pages, and an Accept
#     of both JSON and SSE.
#  2. /__control/tools adds a tool on pages (the mock sends tools
#     list_changed on the gateway's subscriptions/listen stream): the gateway
#     lists 121 tools within 2 s.
#  3. legacy: a 2025-06-18 mock. drop_session forgets the gateway's upstream
#     session; the next tools/call through the gateway still succeeds: the
#     mock refused the old session at the session check (the call's, or the
#     gateway's reopened GET stream's), the gateway initialized again and the
#     call was served.
#  4. slow: a tool that takes 90 s, on a server with timeout_ms 120000 (the
#     tool_call_timeout is 30 s), completes.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<lane db> bash scripts/scenarios/69_m13_upstream.sh
#
# SCN_M13_SLOW_MS (default 90000) sets step 4's tool time; the gate keeps the
# default.
set -u
source "$(dirname "$0")/lib.sh"
source "$(dirname "$0")/lib_mcp.sh"

FIX=$SCN_ROOT/fixtures/M13
SLOW_MS=${SCN_M13_SLOW_MS:-90000}
PAGES=$SCN_BIN/m13-pages # the pages mock's fixtures, written below

# pages_tools <n> [extra]: n tools t000.. as a tools.json list, plus one
# named extra when given.
pages_tools() {
  python3 -I - "$@" <<'PY'
import json, sys
n, extra = int(sys.argv[1]), sys.argv[2:]
tools = [{"name": "t%03d" % i, "description": "M13 paged tool %d" % i,
          "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}} for i in range(n)]
tools += [{"name": e, "description": "added by /__control/tools", "inputSchema": {"type": "object"}} for e in extra]
print(json.dumps(tools))
PY
}

# gw_tool_count: the number of tools the gateway lists on the session.
gw_tool_count() {
  local code
  code=$(scn_mcp_post /mcp '{"jsonrpc":"2.0","id":20,"method":"tools/list"}')
  [ "$code" = 200 ] || { echo "http-$code"; return; }
  scn_json 'len(d["result"]["tools"])'
}

mkdir -p "$SCN_BIN" "$PAGES"
pages_tools 120 >"$PAGES/tools.json"
scn_mcp_up pages --era dual --path /rpc/v2 --fixtures "$PAGES"
scn_mcp_up legacy --era 2025-06-18
scn_mcp_up slow --era 2025-06-18
scn_mcp_control pages '{"sse":true,"page_size":50}' >/dev/null || scn_fail m13.arm "arming pages failed"
scn_mcp_gateway_template "$FIX/gateway.yaml"
SCN_CATALOG_WAIT_SKIP=all scn_up # no managed provider in this config
scn_mcp_session_init 2025-06-18

scn_check "1. a mock at /rpc/v2, in SSE, 120 tools in pages of 50: the gateway lists all 120"
code=$(scn_mcp_post /mcp '{"jsonrpc":"2.0","id":2,"method":"tools/list"}')
scn_assert_eq 1.list_status 200 "$code"
scn_assert_eq 1.pages_tools 120 "$(scn_json 'len([t for t in d["result"]["tools"] if t["name"].startswith("pages_")])')"
scn_mcp_received pages >"$SCN_DIR/pages.json"
scn_assert_jq 1.only_the_configured_path '[.received[] | select(.path != "/rpc/v2" and (.path | startswith("/__") | not))] | length' 0 "$SCN_DIR/pages.json"
scn_assert_jq 1.three_pages '[.received[] | select(.method == "tools/list" and .rejected == null)] | length' 3 "$SCN_DIR/pages.json"
scn_assert_jq 1.era_detected '[.received[] | select(.method == "tools/list")][0].era' 2026-07-28 "$SCN_DIR/pages.json"
scn_assert_jq 1.accept_both \
  '[.received[] | select(.http_method == "POST") | .headers.accept | (contains("application/json") and contains("text/event-stream"))] | all' \
  true "$SCN_DIR/pages.json"
code=$(scn_mcp_post /mcp '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"pages_t007","arguments":{"text":"hi"}}}')
scn_assert_eq 1.sse_call_status 200 "$code"
scn_assert_eq 1.sse_call_text 't007 ok: {"text":"hi"}' "$(scn_json 'd["result"]["content"][0]["text"]')"

scn_check "2. /__control/tools adds a tool: the gateway lists it within 2 s"
scn_mcp_control pages "$(pages_tools 120 added)" tools >/dev/null || scn_fail 2.control "/__control/tools failed"
started=$(python3 -I -c 'import time; print(time.time())')
count=""
for _ in $(seq 1 20); do
  count=$(gw_tool_count)
  [ "$count" = 127 ] && break # 121 on pages, 3 on legacy, 3 on slow
  sleep 0.1
done
elapsed=$(python3 -I -c "import time; print(round(time.time() - $started, 2))")
scn_assert_eq 2.tool_added 127 "$count"
scn_assert_eq 2.within_2s yes "$(python3 -I -c "print('yes' if $elapsed <= 2 else 'no')")"
echo "  (seen after ${elapsed}s)"

scn_check "3. drop_session on the legacy mock is invisible: one refused call, a new session, success"
code=$(scn_mcp_post /mcp '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"legacy_echo","arguments":{"text":"before"}}}')
scn_assert_eq 3.before_drop 200 "$code"
scn_mcp_control legacy '{"drop_session":true}' >"$SCN_DIR/drop.json" || scn_fail 3.drop "drop_session failed"
scn_assert_jq 3.dropped '.dropped_sessions >= 1' true "$SCN_DIR/drop.json"
code=$(scn_mcp_post /mcp '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"legacy_echo","arguments":{"text":"after"}}}')
scn_assert_eq 3.after_drop_status 200 "$code"
scn_assert_eq 3.after_drop_text 'echo ok: {"text":"after"}' "$(scn_json 'd["result"]["content"][0]["text"]')"
scn_assert_eq 3.no_error false "$(scn_json 'd["result"].get("isError", False)')"
scn_mcp_received legacy >"$SCN_DIR/legacy.json"
# The dropped session is refused at the session check either on the call
# itself or, when the gateway's GET stream reopened first (1 s after the drop),
# on that GET; either way the gateway initialized again and served the call.
scn_assert_jq 3.refused_at_session_check \
  '[.received[] | select((.method == "tools/call" or .http_method == "GET") and .rejected == "session_not_found")] | length >= 1' \
  true "$SCN_DIR/legacy.json"
scn_assert_jq 3.served_calls '[.received[] | select(.method == "tools/call" and .rejected == null)] | length' 2 "$SCN_DIR/legacy.json"
scn_assert_jq 3.initialized_again '[.received[] | select(.method == "initialize")] | length >= 2' true "$SCN_DIR/legacy.json"

scn_check "4. a ${SLOW_MS} ms tool on a server with timeout_ms 120000 completes"
scn_mcp_control slow "{\"latency_ms\":$SLOW_MS}" >/dev/null || scn_fail 4.arm "arming slow failed"
started=$(date +%s)
code=$(SCN_MCP_POST_TIMEOUT=150 scn_mcp_post /mcp '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"slow_echo","arguments":{"text":"late"}}}')
took=$(($(date +%s) - started))
scn_assert_eq 4.slow_status 200 "$code"
# The 2026 protocol writer turns a call that outlasts its stream_after into an SSE stream
# (keep-alives, then the result as the last event), so the result is read from
# either body shape.
scn_assert_eq 4.slow_text 'echo ok: {"text":"late"}' \
  "$(scn_json '(d[-1] if isinstance(d, list) else d)["result"]["content"][0]["text"]')"
scn_assert_eq 4.took_the_tool_time yes "$([ "$took" -ge $((SLOW_MS / 1000)) ] && echo yes)"
echo "  (took ${took}s)"
scn_pass
