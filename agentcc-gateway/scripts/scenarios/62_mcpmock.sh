#!/usr/bin/env bash
# The MCP mock (cmd/agentcc-mcpmock) and lib_mcp.sh behind the real gateway.
#  1. lib_mcp.sh starts two mocks (primary: dual era, bearer auth; spare:
#     2026) and then the gateway, whose static YAML server points at primary.
#     The rendered config carries the fixed test state key under mcp:, and
#     AGENTCC_MCP_STATE_KEYS is exported.
#  2. A legacy (2025-06-18) client initializes on the gateway, and its
#     tools/list shows primary's fixture tools; a tools/call returns the
#     mock's answer.
#  3. primary's /__received records the call: the handshake the gateway made,
#     the tools/call, and the configured bearer as presence and hash, never a
#     value, and never the client's own key; /__charges has the call once, at
#     the fixture price; an is_error armed through scn_mcp_control reaches the
#     client; spare received nothing. 3b: scn_mcp_post stops at
#     SCN_MCP_POST_TIMEOUT on a reply that never ends (a subscriptions/listen
#     stream posted straight to spare) and prints "timeout".
#  4. The EXIT trap stops both mocks and the gateway (no listener is left on
#     their ports) after a passing run, after a run that fails once scn_up has
#     returned, and after a run whose scn_up itself fails.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> bash scripts/scenarios/62_mcpmock.sh
#
# Steps 1-3 run in a child of this script (SCN_CHILD_PHASE=inner), so step 4 can
# look at the ports after the child's EXIT trap has run.
set -u
source "$(dirname "$0")/lib.sh"

UPSTREAM_TOKEN=scn-test-mcp-upstream-token # mcpmock-gateway.yaml's bearer for primary
TEMPLATE=$SCN_ROOT/fixtures/mcp/mcpmock-gateway.yaml

sha256() { python3 -I -c 'import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())' "$1"; }

# --- the child: steps 1-3 ---------------------------------------------------
if [ "${SCN_CHILD_PHASE:-}" = inner ]; then
  source "$SCN_ROOT/lib_mcp.sh"
  scn_mcp_up primary --era dual --auth "bearer:$UPSTREAM_TOKEN"
  scn_mcp_up spare --era 2026
  {
    echo "primary $SCN_MCP_PRIMARY_PORT $SCN_MCP_PRIMARY_PID"
    echo "spare $SCN_MCP_SPARE_PORT $SCN_MCP_SPARE_PID"
  } >"$SCN_CHILD_PORTS"
  scn_mcp_gateway_template "${SCN_CHILD_TEMPLATE:-$TEMPLATE}"
  SCN_CATALOG_WAIT_SKIP=all scn_up # no managed provider in mcpmock-gateway.yaml
  echo "gateway ${SCN_GW##*:} $SCN_GW_PID" >>"$SCN_CHILD_PORTS"
  [ "${SCN_CHILD_MODE:-}" = fail_after_up ] && scn_fail child.deliberate "deliberate failure after scn_up (trap check)"

  scn_check "1. two mocks and the gateway; the state key reaches the gateway through its config"
  scn_assert_eq 1.spare_era 2026 "$(curl -sS "$SCN_MCP_SPARE_URL/__health" | jq -r .era)"
  scn_assert_eq 1.primary_auth bearer "$(curl -sS "$SCN_MCP_PRIMARY_URL/__health" | jq -r .auth)"
  scn_assert_eq 1.state_keys_env "$SCN_MCP_STATE_KEY" "${AGENTCC_MCP_STATE_KEYS:-}"
  scn_assert_eq 1.state_keys_in_config 1 "$(grep -c "^  state_keys: \[\"$SCN_MCP_STATE_KEY\"\]" "$SCN_DIR/config.yaml")"

  scn_check "2. a legacy tools/list through the gateway shows the fixture tools"
  scn_mcp_session_init 2025-06-18
  scn_assert_eq 2.session_set yes "$([ -n "$SCN_MCP_SESSION" ] && echo yes)"
  code=$(scn_mcp_post /mcp '{"jsonrpc":"2.0","id":2,"method":"tools/list"}')
  scn_assert_eq 2.tools_list_status 200 "$code"
  scn_assert_eq 2.tools '["primary_delete_file", "primary_echo", "primary_get_weather"]' \
    "$(scn_json 'sorted(t["name"] for t in d["result"]["tools"])')"
  code=$(scn_mcp_post /mcp '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"primary_echo","arguments":{"text":"hello"}}}')
  scn_assert_eq 2.call_status 200 "$code"
  scn_assert_eq 2.call_text 'echo ok: {"text":"hello"}' "$(scn_json 'd["result"]["content"][0]["text"]')"

  scn_check "3. /__received and /__charges record the call; spare received nothing"
  scn_mcp_received primary >"$SCN_DIR/received.json"
  scn_assert_jq 3.handshake_seen \
    '[.received[].method | select(. == "initialize" or . == "notifications/initialized" or . == "tools/list")] | unique | join(",")' \
    'initialize,notifications/initialized,tools/list' "$SCN_DIR/received.json"
  scn_mcp_received primary 1 tools/call >"$SCN_DIR/call.json"
  scn_assert_jq 3.call_name '.received[0].name' echo "$SCN_DIR/call.json"
  scn_assert_jq 3.call_era '.received[0].era' 2025-11-25 "$SCN_DIR/call.json"
  scn_assert_jq 3.auth_present '.received[0].auth.present' true "$SCN_DIR/call.json"
  scn_assert_jq 3.auth_is_the_configured_bearer '.received[0].auth.sha256' "$(sha256 "Bearer $UPSTREAM_TOKEN")" "$SCN_DIR/call.json"
  scn_assert_eq 3.no_credential_value 0 "$(grep -c -e "$UPSTREAM_TOKEN" -e "$SCN_KEY" "$SCN_DIR/received.json")"
  scn_assert_eq 3.client_key_not_forwarded 0 "$(grep -c "$(sha256 "Bearer $SCN_KEY")" "$SCN_DIR/received.json")"
  scn_mcp_charges primary >"$SCN_DIR/charges.json"
  scn_assert_jq 3.charge '[.charges[0].tool, .charges[0].price_usd, .charges[0].completed] | map(tostring) | join(" ")' \
    'echo 0.001 true' "$SCN_DIR/charges.json"
  scn_assert_jq 3.charge_count '.count' 1 "$SCN_DIR/charges.json"
  scn_assert_jq 3.charge_total_micros '.total_micros' 1000 "$SCN_DIR/charges.json"
  scn_mcp_control primary '{"is_error":true}' >/dev/null || scn_fail 3.control "scn_mcp_control primary failed"
  code=$(scn_mcp_post /mcp '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"primary_echo","arguments":{"text":"again"}}}')
  scn_assert_eq 3.armed_call_status 200 "$code"
  scn_assert_eq 3.armed_is_error true "$(scn_json 'd["result"].get("isError", False)')"
  scn_assert_eq 3.spare_received 0 "$(scn_mcp_received spare | jq '.received | length')"

  scn_check "3b. scn_mcp_post gives up on a reply that streams forever (spare's subscriptions/listen)"
  listen='{"jsonrpc":"2.0","id":9,"method":"subscriptions/listen","params":{"notifications":["notifications/tools/list_changed"],"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{},"io.modelcontextprotocol/clientInfo":{"name":"scn-mcp","version":"1"}}}}'
  started=$(date +%s)
  code=$(SCN_GW=$SCN_MCP_SPARE_URL SCN_MCP_POST_TIMEOUT=2 SCN_MCP_SESSION='' \
    scn_mcp_post /mcp "$listen" 'MCP-Protocol-Version: 2026-07-28' 'Mcp-Method: subscriptions/listen' 2>/dev/null)
  scn_assert_eq 3b.listen_times_out timeout "$code"
  scn_assert_eq 3b.within_bound yes "$([ $(($(date +%s) - started)) -le 6 ] && echo yes)"
  scn_assert_eq 3b.stream_was_open 200 "$(sed -n '1s/^HTTP\/[0-9.]* \([0-9]*\).*/\1/p' "$SCN_DIR/last.headers")"
  scn_pass
  exit 0
fi

# --- this script: step 4 ----------------------------------------------------
OUT=$SCN_HOME/scn-mcp/62_mcpmock-traps
rm -rf "$OUT"
mkdir -p "$OUT"

listening() { # port: "yes" if something accepts on 127.0.0.1:port
  python3 -I -c '
import socket, sys
s = socket.socket(); s.settimeout(1)
try:
    s.connect(("127.0.0.1", int(sys.argv[1]))); print("yes")
except OSError:
    print("no")' "$1"
}

# run_child <mode> [env args...]: run steps 1-3 as a child; sets CHILD_STATUS.
# The env args go first to env(1), so -u NAME may lead them.
run_child() {
  local mode=$1
  shift
  env "$@" SCN_CHILD_PHASE=inner SCN_CHILD_MODE="$mode" SCN_CHILD_PORTS="$OUT/$mode.ports" bash "$0"
  CHILD_STATUS=$?
}

# assert_released <mode> <want lines>: every port the child recorded is free
# and every pid is gone.
assert_released() {
  local mode=$1 want=$2 name port pid n=0
  scn_assert_eq "4.$mode.recorded" "$want" "$(wc -l <"$OUT/$mode.ports" | tr -d ' ')"
  while read -r name port pid; do
    n=$((n + 1))
    scn_assert_eq "4.$mode.$name.listening" no "$(listening "$port")"
    scn_assert_eq "4.$mode.$name.pid_gone" gone "$(kill -0 "$pid" 2>/dev/null && echo alive || echo gone)"
  done <"$OUT/$mode.ports"
}

run_child pass
scn_check "4. the EXIT trap stops both mocks and the gateway"
scn_assert_eq 4.pass.status 0 "$CHILD_STATUS"
assert_released pass 3

# A deliberate failure must not reach run_all's fail file.
run_child fail_after_up -u SCN_FAIL_FILE SCN_SKIP_BUILD=1 >"$OUT/fail_after_up.log" 2>&1
scn_assert_eq 4.fail_after_up.status 1 "$CHILD_STATUS"
scn_assert_eq 4.fail_after_up.failed_where 1 "$(grep -c 'SCN FAIL \[child.deliberate\]' "$OUT/fail_after_up.log")"
assert_released fail_after_up 3

# A gateway that refuses its config makes scn_up itself fail.
sed 's/api_format: "openai"/api_format: ""/' "$TEMPLATE" >"$OUT/broken.yaml"
run_child scn_up_fails -u SCN_FAIL_FILE SCN_SKIP_BUILD=1 SCN_CHILD_TEMPLATE="$OUT/broken.yaml" >"$OUT/scn_up_fails.log" 2>&1
scn_assert_eq 4.scn_up_fails.status 1 "$CHILD_STATUS"
scn_assert_eq 4.scn_up_fails.failed_where 1 "$(grep -c 'SCN FAIL \[scn_up\]: gateway exited during startup' "$OUT/scn_up_fails.log")"
assert_released scn_up_fails 2
scn_pass
