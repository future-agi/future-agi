#!/usr/bin/env bash
# The SSRF guard and POST /-/mcp/probe behind the real gateway.
#  1. The gateway starts with mcp.egress.private_allow listing the three MCP
#     mocks and the HTTP mock by their loopback ports; the probe route needs the
#     admin token and refuses a credential_id or an egress key in its body.
#  2. Probing the dual-era mock returns ok, era 2026-07-28, the fixture's
#     tool count and the canonical tools hash (computed here in Python).
#  3. A 2025-06-18 mock that wants a bearer: with the inline bearer the probe
#     is ok and the mock saw that bearer (by hash) on every request, and the
#     probe ended its session; without it, auth_required; the token is in
#     neither the response nor the gateway's debug log.
#  4. A 2024-11-05 HTTP+SSE mock is detected as era 2024-11-05.
#  5. http://169.254.169.254/ is egress_refused; a loopback port not in the
#     list (the gateway's own) is egress_refused; plaintext http to a public
#     address is https_required (refused before any connection).
#  6. The HTTP mock, a server that is not MCP, is not_mcp.
#  7. The probe stored nothing: /-/mcp/status lists no server and no tool.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<lane db> bash scripts/scenarios/71_m17_probe.sh
#
# The gateway is started here rather than with scn_up because the HTTP mock's
# port must be in private_allow before the gateway starts, and scn_up picks
# that port itself. This scenario makes no model call and uses no Redis.
set -u
source "$(dirname "$0")/lib.sh"
source "$(dirname "$0")/lib_mcp.sh"

M17_TOKEN=scn-m17-upstream-bearer-not-a-secret # the legacy mock's bearer; fake
TEMPLATE=$SCN_ROOT/fixtures/M17/gateway.yaml

sha256() { python3 -I -c 'import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())' "$1"; }

m17_down() {
  _scn_mcp_down
  scn_down
}

m17_build() { # binary package
  [ -n "${SCN_SKIP_BUILD:-}" ] && [ -x "$SCN_BIN/$1" ] && return 0
  (cd "$SCN_GATEWAY_DIR" && go build -o "$SCN_BIN/$1" "$2") >>"$SCN_DIR/build.log" 2>&1 ||
    scn_fail m17_up "building $1 failed: $(tail -5 "$SCN_DIR/build.log")"
}

m17_up() {
  local tool x0_port gw_port src
  for tool in go curl jq python3; do
    command -v "$tool" >/dev/null || scn_fail m17_up "$tool is required"
  done
  rm -rf "$SCN_DIR"
  mkdir -p "$SCN_DIR" "$SCN_BIN"
  touch "$SCN_ORGS_FILE" # scn_down's Redis cleanup reads it; this run tracks no org
  scn_mcp_up main --era dual
  scn_mcp_up legacy --era 2025-06-18 --auth "bearer:$M17_TOKEN"
  scn_mcp_up sse --era 2024-11-05
  trap m17_down EXIT
  trap 'exit 130' INT TERM
  m17_build agentcc ./cmd/agentcc
  m17_build agentcc-mockup ./cmd/agentcc-mockup

  x0_port=$(_scn_free_port)
  SCN_MOCK="http://127.0.0.1:$x0_port"
  "$SCN_BIN/agentcc-mockup" --addr "127.0.0.1:$x0_port" --fixtures "$SCN_ROOT/fixtures/common" >"$SCN_DIR/mock.log" 2>&1 &
  SCN_MOCK_PID=$!
  _scn_wait_http mock "$SCN_MOCK/__health" 15 "$SCN_MOCK_PID"

  src=$SCN_BIN/m17-gateway.yaml
  sed -e "s/@@M17_MAIN_PORT@@/$SCN_MCP_MAIN_PORT/" -e "s/@@M17_LEGACY_PORT@@/$SCN_MCP_LEGACY_PORT/" \
    -e "s/@@M17_SSE_PORT@@/$SCN_MCP_SSE_PORT/" -e "s/@@M17_X0_PORT@@/$x0_port/" "$TEMPLATE" >"$src"
  grep -q '@@M17_' "$src" && scn_fail m17_up "unrendered placeholders in $src"
  scn_mcp_gateway_template "$src"

  gw_port=$(_scn_free_port)
  SCN_GW="http://127.0.0.1:$gw_port"
  SCN_T_GATEWAY_PORT=$gw_port SCN_T_GATEWAY_URL=$SCN_GW SCN_T_MOCK_URL=$SCN_MOCK \
    SCN_T_ADMIN_TOKEN=$SCN_ADMIN_TOKEN SCN_T_SCN_DIR=$SCN_DIR SCN_T_SCN_ROOT=$SCN_ROOT SCN_T_LANE=$SCN_LANE \
    SCN_T_REDIS="" SCN_T_REDIS_DB=0 SCN_T_OTEL="" \
    _scn_render_config "$SCN_CONFIG_TEMPLATE" "$SCN_DIR/config.yaml"
  # Only the rendered config, the port and the state key configure the gateway.
  local unset_env=() var
  for var in $(env | sed -n 's/^\(AGENTCC_[A-Za-z0-9_]*\)=.*/\1/p'); do unset_env+=(-u "$var"); done
  env ${unset_env[@]+"${unset_env[@]}"} AGENTCC_PORT="$gw_port" AGENTCC_MCP_STATE_KEYS="$SCN_MCP_STATE_KEY" \
    "$SCN_BIN/agentcc" --config "$SCN_DIR/config.yaml" >"$SCN_DIR/gateway.log" 2>&1 &
  SCN_GW_PID=$!
  _scn_wait_http gateway "$SCN_GW/healthz" 30 "$SCN_GW_PID"
  export SCN_GW SCN_MOCK SCN_DIR
  echo "m17_up: gateway $SCN_GW, HTTP mock $SCN_MOCK, MCP mocks main $SCN_MCP_MAIN_PORT, legacy $SCN_MCP_LEGACY_PORT, sse $SCN_MCP_SSE_PORT; logs in $SCN_DIR"
}

# m17_probe <name> <json>: POST /-/mcp/probe with the admin token; keeps the
# body as $SCN_DIR/<name>.json and asserts HTTP 200.
m17_probe() {
  local code
  code=$(scn_admin POST /-/mcp/probe "$2")
  cp "$SCN_DIR/last.body" "$SCN_DIR/$1.json"
  scn_assert_eq "$1.status" 200 "$code"
}

m17_up

# The fixture's tool count and canonical hash, computed independently.
read -r WANT_COUNT WANT_HASH < <(python3 -I - "$SCN_MCP_FIXTURES/tools.json" <<'PY'
import hashlib, json, sys
tools = json.load(open(sys.argv[1], encoding="utf-8"))
keys = ["name", "title", "description", "inputSchema", "outputSchema", "annotations"]
proj = sorted(({k: t[k] for k in keys if k in t} for t in tools), key=lambda t: t.get("name", ""))
canon = json.dumps(proj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
print(len(tools), hashlib.sha256(canon.encode()).hexdigest())
PY
)

scn_check "1. the probe route: admin only, strict body"
scn_assert_eq 1.config_lists_ports 4 "$(grep -c '^      - "127.0.0.1:[0-9]*"$' "$SCN_DIR/config.yaml")"
# The bodies are built outside "$(...)": macOS bash 3.2 brace-expands a
# double-quoted JSON object with a comma nested in a quoted command
# substitution, so these checks sent a body that was not JSON.
probe_ok="{\"org_id\":\"org-m17\",\"url\":\"$SCN_MCP_MAIN_URL/mcp\"}"
probe_cred="{\"org_id\":\"org-m17\",\"url\":\"$SCN_MCP_MAIN_URL/mcp\",\"credential_id\":\"cred_1\"}"
probe_private="{\"org_id\":\"org-m17\",\"url\":\"$SCN_MCP_MAIN_URL/mcp\",\"private_network\":true}"
scn_assert_eq 1.no_admin_token 401 "$(SCN_ADMIN_TOKEN=wrong-token scn_admin POST /-/mcp/probe "$probe_ok")"
scn_assert_eq 1.credential_id_refused 400 "$(scn_admin POST /-/mcp/probe "$probe_cred")"
scn_assert_jq 1.refusal_code '.error' invalid_probe_request
scn_assert_jq 1.refusal_names_credential_id '.message | test("credential_id")' true
scn_assert_eq 1.private_network_refused 400 "$(scn_admin POST /-/mcp/probe "$probe_private")"
scn_assert_jq 1.private_network_names_it '.message | test("private_network")' true

scn_check "2. the dual-era mock: ok, era 2026-07-28, the fixture's tools and hash"
m17_probe 2.main "{\"org_id\":\"org-m17\",\"url\":\"$SCN_MCP_MAIN_URL/mcp\"}"
scn_assert_jq 2.ok '.ok' true
scn_assert_jq 2.era '.era' 2026-07-28
scn_assert_jq 2.tool_count '.tool_count' "$WANT_COUNT"
scn_assert_jq 2.tools_hash '.tools_hash' "$WANT_HASH"
scn_assert_jq 2.server_info '.server_info.name' mcpmock-main
scn_assert_jq 2.error_null '.error' null
scn_assert_jq 2.latency '.latency_ms | type' number
scn_mcp_received main >"$SCN_DIR/main-received.json"
scn_assert_jq 2.upstream_saw '[.received[].method] | unique | join(",")' 'server/discover,tools/list' "$SCN_DIR/main-received.json"

scn_check "3. inline bearer: used on every request, never shown or logged"
m17_probe 3.legacy "{\"org_id\":\"org-m17\",\"url\":\"$SCN_MCP_LEGACY_URL/mcp\",\"auth\":{\"type\":\"bearer\",\"value\":\"$M17_TOKEN\"}}"
scn_assert_jq 3.ok '.ok' true
scn_assert_jq 3.era '.era' 2025-06-18
scn_assert_jq 3.tool_count '.tool_count' "$WANT_COUNT"
scn_assert_jq 3.tools_hash '.tools_hash' "$WANT_HASH"
scn_mcp_received legacy >"$SCN_DIR/legacy-received.json"
scn_assert_jq 3.every_request_had_the_bearer \
  "[.received[] | .auth.sha256 == \"$(sha256 "Bearer $M17_TOKEN")\"] | all" true "$SCN_DIR/legacy-received.json"
scn_assert_jq 3.handshake '[.received[] | .method // .http_method] | unique | join(",")' \
  'DELETE,initialize,notifications/initialized,server/discover,tools/list' "$SCN_DIR/legacy-received.json"
m17_probe 3.no_auth "{\"org_id\":\"org-m17\",\"url\":\"$SCN_MCP_LEGACY_URL/mcp\"}"
scn_assert_jq 3.no_auth_error '.error' auth_required
scn_assert_eq 3.token_not_in_responses 0 "$(cat "$SCN_DIR"/3.*.json | grep -c "$M17_TOKEN")"
scn_assert_eq 3.token_not_in_gateway_log 0 "$(grep -c "$M17_TOKEN" "$SCN_DIR/gateway.log")"
scn_assert_eq 3.probe_logged yes "$(grep -q 'mcp probe' "$SCN_DIR/gateway.log" && echo yes || echo no)"

scn_check "4. the 2024-11-05 HTTP+SSE mock"
m17_probe 4.sse "{\"org_id\":\"org-m17\",\"url\":\"$SCN_MCP_SSE_URL/sse\"}"
scn_assert_jq 4.ok '.ok' true
scn_assert_jq 4.era '.era' 2024-11-05
scn_assert_jq 4.tools_hash '.tools_hash' "$WANT_HASH"

scn_check "5. the guard: metadata, unlisted loopback and plaintext to a public address"
m17_probe 5.metadata '{"org_id":"org-m17","url":"http://169.254.169.254/"}'
scn_assert_jq 5.metadata_ok '.ok' false
scn_assert_jq 5.metadata_error '.error' egress_refused
scn_assert_jq 5.metadata_no_details '[.era, .tool_count, .tools_hash] | map(. == null) | all' true
m17_probe 5.unlisted "{\"org_id\":\"org-m17\",\"url\":\"$SCN_GW/healthz\"}"
scn_assert_jq 5.unlisted_error '.error' egress_refused
m17_probe 5.plaintext '{"org_id":"org-m17","url":"http://192.0.2.10:9/mcp"}'
scn_assert_jq 5.plaintext_error '.error' https_required
scn_assert_eq 5.refusals_logged yes "$(grep -q 'mcp egress refused a connection' "$SCN_DIR/gateway.log" && echo yes || echo no)"

scn_check "6. the X0 mock is not MCP"
m17_probe 6.x0 "{\"org_id\":\"org-m17\",\"url\":\"$SCN_MOCK/\"}"
scn_assert_jq 6.ok '.ok' false
scn_assert_jq 6.error '.error' not_mcp

scn_check "7. the probe stored nothing"
scn_assert_eq 7.status 200 "$(scn_admin GET /-/mcp/status?org_id=org-m17)"
scn_assert_jq 7.no_servers '.servers | length' 0
scn_assert_jq 7.no_tools '.tools' 0

scn_pass
