#!/usr/bin/env bash
# Identity on every MCP method, behind the real gateway with request auth
# OFF (fixtures/M8/gateway.yaml, its YAML server bound to org-acme by
# mcp.static.org_id), so every 401 below is the MCP identity's own.
#  1. With no credential, POST initialize, GET /mcp and DELETE /mcp all answer
#     401 with the gateway's Bearer challenge and a JSON-RPC error -32001,
#     data.code missing_credentials; a bad key is invalid_api_key. Every
#     answer names its request with x-agentcc-request-id mcp_<ULID>.
#  2. A valid key with x-omega-run-id: R1 and a _meta step of plan lists the
#     mock's tools.
#  3. A tools/call sent with every agent header, the framework aliases, an
#     inbound depth header, an inbound x-api-key and io.futureagi/* _meta keys
#     reaches the mock with none of them: /__received shows no agent, alias or
#     depth header (primary is not marked propagate_depth), no io.futureagi/*
#     key, and the server's own bearer as the only credential (by hash).
#  4. The same field with different values in x-agentcc-* and x-omega-* is
#     400, JSON-RPC -32602, data.code agent_header_conflict.
#  5. A key revoked through the admin API is 401 on its next request, never
#     200.
#  6. Restarted on fixtures/M8/gateway-no-static-org.yaml (request auth off,
#     the YAML server bound to no org: the release line's setup), a request
#     with no credential is served as on the release line:
#     initialize, tools/list of the operator's tools and a
#     tools/call that reaches the mock. A bad key is still 401
#     invalid_api_key, and a keyless request to a virtual server is 401
#     missing_credentials.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<lane db> bash scripts/scenarios/64_m8_identity.sh
set -u
source "$(dirname "$0")/lib.sh"
source "$(dirname "$0")/lib_mcp.sh"

UPSTREAM_TOKEN=scn-test-mcp-upstream-token # gateway.yaml's bearer for primary
REVOCABLE_KEY=scn-test-key-m8-revocable    # gateway.yaml's second key
TEMPLATE=$SCN_ROOT/fixtures/M8/gateway.yaml
TRACEPARENT=00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01

sha256() { python3 -I -c 'import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())' "$1"; }

# m8_raw <method> <path> [body] [header...]: a request with no credential
# unless a header adds one. Prints the HTTP status; the answer lands in
# $SCN_DIR/last.{headers,body}.
m8_raw() {
  local method=$1 path=$2 body=${3:-} h args
  shift 3 2>/dev/null || shift $#
  args=(-sS --max-time 10 -o "$SCN_DIR/last.body" -D "$SCN_DIR/last.headers" -w '%{http_code}' -X "$method" "$SCN_GW$path"
    -H 'Accept: application/json, text/event-stream')
  [ -n "$body" ] && args+=(-H 'Content-Type: application/json' --data-binary "$body")
  for h in "$@"; do args+=(-H "$h"); done
  curl "${args[@]}"
}

# m8_assert_401 <name> <data.code>: the last answer is the MCP identity's 401.
m8_assert_401() {
  scn_assert_eq "$1.www_authenticate" 'Bearer realm="agentcc-gateway"' "$(scn_hdr WWW-Authenticate)"
  scn_assert_jq "$1.jsonrpc_code" '.error.code' -32001
  scn_assert_jq "$1.data_code" '.error.data.code' "$2"
  scn_assert_eq "$1.no_session" "" "$(scn_hdr Mcp-Session-Id)"
  scn_assert_eq "$1.request_id" yes "$(scn_hdr x-agentcc-request-id | grep -Eq '^mcp_[0-9A-HJKMNP-TV-Z]{26}$' && echo yes)"
}

# The mock serves MCP on every path (--path /), so the gateway reaches it both
# with the older client (it posts to <url>/mcp) and the newer one (it posts
# to the configured URL exactly). This scenario is about identity, not the URL.
scn_mcp_up primary --era dual --path / --auth "bearer:$UPSTREAM_TOKEN"
scn_mcp_gateway_template "$TEMPLATE"
SCN_CATALOG_WAIT_SKIP=all scn_up # no managed provider in the template
INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"scn-m8","version":"1"}}}'

scn_check "1. no credential: 401 with the Bearer challenge on POST, GET and DELETE"
scn_assert_eq 1.config_auth_off 1 "$(grep -c '^  enabled: false' "$SCN_DIR/config.yaml")"
scn_assert_eq 1.post_initialize 401 "$(m8_raw POST /mcp "$INIT")"
m8_assert_401 1.post missing_credentials
scn_assert_eq 1.get 401 "$(m8_raw GET /mcp '' 'Mcp-Session-Id: none')"
m8_assert_401 1.get missing_credentials
scn_assert_eq 1.delete 401 "$(m8_raw DELETE /mcp '' 'Mcp-Session-Id: none')"
m8_assert_401 1.delete missing_credentials
scn_assert_eq 1.bad_key 401 "$(m8_raw POST /mcp "$INIT" 'Authorization: Bearer scn-not-a-key')"
m8_assert_401 1.bad_key invalid_api_key
scn_assert_eq 1.bad_x_api_key 401 "$(m8_raw GET /mcp '' 'x-api-key: scn-not-a-key')"
m8_assert_401 1.bad_x_api_key invalid_api_key
# The admin status names the org: /-/mcp/status requires org_id,
# and the status before it ignores the parameter and counts every session.
# the org-scoped status counts no session (sessions are not bound to an org),
# so after that merge this check is 0 by construction; the proof on any tree
# is each 401's no_session above and TestAnonymousPostGetDelete401's
# SessionCount.
scn_assert_eq 1.status_lists_no_session 0 "$(scn_admin GET '/-/mcp/status?org_id=org-acme' >/dev/null; jq -r .sessions "$SCN_DIR/last.body")"

scn_check "2. a valid key with x-omega-run-id and a _meta step lists the tools"
scn_mcp_session_init 2025-06-18
code=$(scn_mcp_post /mcp '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{"_meta":{"io.futureagi/agent":{"step":"plan"}}}}' 'x-omega-run-id: R1')
scn_assert_eq 2.tools_list_status 200 "$code"
scn_assert_eq 2.tools '["primary_delete_file", "primary_echo", "primary_get_weather"]' \
  "$(scn_json 'sorted(t["name"] for t in d["result"]["tools"])')"
scn_assert_eq 2.request_id yes "$(scn_hdr x-agentcc-request-id | grep -Eq '^mcp_[0-9A-HJKMNP-TV-Z]{26}$' && echo yes)"

scn_check "3. a tools/call reaches the mock with no agent header, alias, depth header, inbound credential or io.futureagi/* key"
CALL='{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"primary_echo","arguments":{"text":"hello"},"_meta":{"progressToken":"p-m8","traceparent":"'$TRACEPARENT'","io.futureagi/agent":{"step":"plan","task_id":"t-1"},"io.futureagi/generation_id":"gen-m8"}}}'
code=$(scn_mcp_post /mcp "$CALL" 'x-omega-run-id: R1' 'x-agentcc-agent-id: planner' 'x-agentcc-step: plan' \
  'x-agentcc-session-id: s-m8' 'x-omega-workflow: wf' 'x-agentcc-generation-id: gen-m8' 'x-thread-id: thr-1' \
  'x-resource-id: res-1' 'x-claude-code-session-id: cc-1' 'x-a2a-task-id: a2a-1' "traceparent: $TRACEPARENT" \
  'x-session-id: generic-1' 'X-Agentcc-Agent-Depth: 1' "x-api-key: $REVOCABLE_KEY")
scn_assert_eq 3.call_status 200 "$code"
scn_assert_eq 3.call_text 'echo ok: {"text":"hello"}' "$(scn_json 'd["result"]["content"][0]["text"]')"
scn_mcp_received primary 1 tools/call >"$SCN_DIR/received.json"
scn_assert_jq 3.one_call '.received | length' 1 "$SCN_DIR/received.json"
# The mock records the headers it got, so the absences below are not vacuous.
scn_assert_jq 3.headers_recorded '.received[0].headers["content-type"]' application/json "$SCN_DIR/received.json"
scn_assert_jq 3.no_agent_or_alias_header \
  '[.received[0].headers // {} | keys[] | ascii_downcase
    | select(startswith("x-agentcc-") or startswith("x-omega-") or . == "x-thread-id" or . == "x-resource-id"
      or . == "x-claude-code-session-id" or . == "x-a2a-context-id" or . == "x-a2a-task-id" or . == "traceparent"
      or . == "x-session-id")] | join(",")' "" "$SCN_DIR/received.json"
scn_assert_jq 3.no_depth_header '[.received[0].headers // {} | keys[] | ascii_downcase | select(. == "x-agentcc-agent-depth")] | length' 0 "$SCN_DIR/received.json"
scn_assert_jq 3.no_gateway_meta '[.received[0]._meta // {} | keys[] | select(startswith("io.futureagi/") or . == "traceparent")] | length' 0 "$SCN_DIR/received.json"
scn_assert_jq 3.auth_is_the_servers_own '.received[0].auth.sha256' "$(sha256 "Bearer $UPSTREAM_TOKEN")" "$SCN_DIR/received.json"
scn_assert_jq 3.no_other_credential '.received[0].credentials // {} | length' 0 "$SCN_DIR/received.json"
scn_assert_eq 3.no_inbound_key_value 0 "$(grep -c -e "$SCN_KEY" -e "$REVOCABLE_KEY" "$SCN_DIR/received.json")"
scn_assert_eq 3.no_inbound_key_hash 0 "$(grep -c -e "$(sha256 "Bearer $SCN_KEY")" -e "$(sha256 "$REVOCABLE_KEY")" "$SCN_DIR/received.json")"
# Whole words only (-w): the gateway's own progressToken is "agentcc-<16 hex>-<n>", so a bare cc-1 matched it
# whenever the hex began with 1 (job j-20261024t2029-253336), though no agent value reached the upstream.
scn_assert_eq 3.no_agent_values 0 "$(grep -c -w -e gen-m8 -e thr-1 -e planner -e cc-1 -e generic-1 "$SCN_DIR/received.json")"

scn_check "4. one field, two values in the two namespaces: 400 agent_header_conflict"
code=$(scn_mcp_post /mcp '{"jsonrpc":"2.0","id":4,"method":"tools/list"}' 'x-agentcc-run-id: a' 'x-omega-run-id: b')
scn_assert_eq 4.status 400 "$code"
scn_assert_jq 4.jsonrpc_code '.error.code' -32602
scn_assert_jq 4.data_code '.error.data.code' agent_header_conflict
scn_assert_jq 4.data_param '.error.data.param' x-omega-run-id

scn_check "5. a key revoked through the admin API is 401 on its next request"
scn_assert_eq 5.before 200 "$(m8_raw POST /mcp "$INIT" "Authorization: Bearer $REVOCABLE_KEY")"
scn_admin GET /-/keys >/dev/null
KEY_ID=$(jq -r '[(.keys // .data // .)[] | select(.name == "acme-revocable") | .id][0]' "$SCN_DIR/last.body")
[ -n "$KEY_ID" ] && [ "$KEY_ID" != null ] || scn_fail 5.key_id "no acme-revocable key in /-/keys: $(cat "$SCN_DIR/last.body")"
scn_assert_eq 5.revoke 200 "$(scn_admin DELETE "/-/keys/$KEY_ID")"
scn_assert_eq 5.after 401 "$(m8_raw POST /mcp "$INIT" "Authorization: Bearer $REVOCABLE_KEY")"
m8_assert_401 5.after invalid_api_key
scn_assert_eq 5.after_get 401 "$(m8_raw GET /mcp '' "x-api-key: $REVOCABLE_KEY")"
scn_assert_eq 5.other_key_still_works 200 "$(scn_mcp_post /mcp '{"jsonrpc":"2.0","id":5,"method":"tools/list"}')"

scn_check "6. request auth off, the YAML server bound to no org: a keyless request is served it, as on the release line"
scn_down
scn_mcp_gateway_template "$SCN_ROOT/fixtures/M8/gateway-no-static-org.yaml"
SCN_SKIP_BUILD=1 SCN_CATALOG_WAIT_SKIP=all scn_up
scn_assert_eq 6.config_auth_off 1 "$(grep -c '^  enabled: false' "$SCN_DIR/config.yaml")"
scn_assert_eq 6.config_no_static_org 0 "$(grep -c '^  static:' "$SCN_DIR/config.yaml")"
scn_assert_eq 6.keyless_initialize 200 "$(m8_raw POST /mcp "$INIT")"
KEYLESS_SESSION=$(scn_hdr Mcp-Session-Id)
scn_assert_eq 6.keyless_session yes "$([ -n "$KEYLESS_SESSION" ] && echo yes)"
scn_assert_eq 6.keyless_initialized 202 "$(m8_raw POST /mcp '{"jsonrpc":"2.0","method":"notifications/initialized"}' "Mcp-Session-Id: $KEYLESS_SESSION")"
scn_assert_eq 6.keyless_tools_list 200 "$(m8_raw POST /mcp '{"jsonrpc":"2.0","id":6,"method":"tools/list"}' "Mcp-Session-Id: $KEYLESS_SESSION")"
scn_assert_eq 6.keyless_tools '["primary_delete_file", "primary_echo", "primary_get_weather"]' \
  "$(scn_json 'sorted(t["name"] for t in d["result"]["tools"])')"
scn_assert_eq 6.keyless_call 200 "$(m8_raw POST /mcp '{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"primary_echo","arguments":{"text":"keyless"}}}' "Mcp-Session-Id: $KEYLESS_SESSION")"
scn_assert_eq 6.keyless_call_text 'echo ok: {"text":"keyless"}' "$(scn_json 'd["result"]["content"][0]["text"]')"
# Section 3's call was the mock's only tools/call (3.one_call), so this one is the second; the gateway sends the
# server's own bearer, as for a keyed call.
scn_mcp_received primary 0 tools/call >"$SCN_DIR/received-6.json"
scn_assert_jq 6.keyless_call_reached_the_mock '.received | length' 2 "$SCN_DIR/received-6.json"
scn_assert_jq 6.keyless_call_auth_is_the_servers_own '.received[-1].auth.sha256' "$(sha256 "Bearer $UPSTREAM_TOKEN")" "$SCN_DIR/received-6.json"
scn_assert_eq 6.bad_key 401 "$(m8_raw POST /mcp "$INIT" 'Authorization: Bearer scn-not-a-key')"
m8_assert_401 6.bad_key invalid_api_key
scn_assert_eq 6.keyless_virtual_server 401 "$(m8_raw POST /mcp/v/vs-none "$INIT")"
m8_assert_401 6.keyless_virtual_server missing_credentials
scn_pass
