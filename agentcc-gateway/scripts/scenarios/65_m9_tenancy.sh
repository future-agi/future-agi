#!/usr/bin/env bash
# M9: per-org MCP tenancy behind the real gateway.
#  1. Orgs A and B each push a server named github, pointing at a different
#     mock (A's with a push-only bearer secret); B also pushes "late", whose
#     mock is not running yet.
#  2. GET /-/mcp/status?org_id=A shows one server, github, healthy; A's mock
#     saw A's pushed bearer. Every /-/mcp/* call needs org_id (400) and an
#     org the gateway knows (404).
#  3. A's tool list holds only A's tools, B's only B's, and neither holds the
#     operator's YAML server, which only mcp.static.org_id sees.
#  4. B's "late" is listed with last_error and becomes healthy within 10 s of
#     its mock starting.
#  5. A policy-only push (mcp.access) reconnects nothing on A's mock.
#  6. GET /-/orgs/A/config and /-/orgs/configs show no secret.
#  7. (SCN_WAVE_GATE=1 only) A's key lists only A's tools on /mcp and its
#     call lands only on A's mock; /-/mcp/test for A lands only on A's mock.
#     These need M8 (the key's org on the principal), M10 (lists from the
#     org's pool) and M13 (calls through it), which merge in this wave; the
#     W3b gate sets SCN_WAVE_GATE=1 when it reruns this script.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<lane db> bash scripts/scenarios/65_m9_tenancy.sh
#
# The mocks answer MCP on every path (--path /), so both today's client,
# which posts to <url>/mcp, and M13's, which posts to <url> exactly, reach
# them. This scenario makes no model call.
set -u
source "$(dirname "$0")/lib.sh"
source "$(dirname "$0")/lib_mcp.sh"

M9_A=org-m9-a
M9_B=org-m9-b
M9_OPS=org-m9-ops
M9_A_TOKEN=scn-m9-org-a-upstream-bearer-not-a-secret # fake; A's mock requires it
M9_A_KEY=scn-test-key-m9-org-a
M9_B_KEY=scn-test-key-m9-org-b
M9_FIX=$SCN_ROOT/fixtures/M9

sha256() { python3 -I -c 'import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())' "$1"; }

# m9_up: three mocks (A, B, the operator's ops), a port for "late", the
# rendered config, then the gateway.
m9_up() {
  scn_mcp_up a --path / --fixtures "$M9_FIX/a" --auth "bearer:$M9_A_TOKEN"
  scn_mcp_up b --path / --fixtures "$M9_FIX/b"
  scn_mcp_up ops --path / --fixtures "$M9_FIX/ops"
  M9_LATE_PORT=$(_scn_free_port)
  local src=$SCN_BIN/m9-gateway.yaml
  mkdir -p "$SCN_BIN"
  sed -e "s/@@M9_A_PORT@@/$SCN_MCP_A_PORT/" -e "s/@@M9_B_PORT@@/$SCN_MCP_B_PORT/" \
    -e "s/@@M9_OPS_PORT@@/$SCN_MCP_OPS_PORT/" -e "s/@@M9_LATE_PORT@@/$M9_LATE_PORT/" "$M9_FIX/gateway.yaml" >"$src"
  grep -q '@@M9_' "$src" && scn_fail m9_up "unrendered placeholders in $src"
  scn_mcp_gateway_template "$src"
  SCN_CATALOG_WAIT_SKIP=all scn_up
}

# m9_start_late: start the "late" mock on its reserved port; lib_mcp.sh's
# exit trap stops it with the others.
m9_start_late() {
  local deadline
  "$SCN_MCPMOCK_BIN" --mode http --addr "127.0.0.1:$M9_LATE_PORT" --name mcpmock-late --era dual --path / \
    --fixtures "$M9_FIX/b" >"$SCN_MCP_DIR/late.log" 2>&1 &
  _SCN_MCP_PIDS+=("$!")
  export SCN_MCP_LATE_URL="http://127.0.0.1:$M9_LATE_PORT"
  deadline=$(( $(date +%s) + 15 ))
  until curl -sf -o /dev/null "$SCN_MCP_LATE_URL/__health"; do
    [ "$(date +%s)" -ge "$deadline" ] && scn_fail m9_start_late "late mock not ready: $(tail -5 "$SCN_MCP_DIR/late.log")"
    sleep 0.1
  done
}

# m9_tools <org>: the sorted "<server_id>:<name>" list of GET /-/mcp/tools.
m9_tools() {
  scn_admin GET "/-/mcp/tools?org_id=$1" >/dev/null
  jq -r '[.tools[] | "\(.server_id):\(.name)"] | sort | join(",")' <"$SCN_DIR/last.body"
}

# m9_has <list> <regex>: true when the comma list has a name matching regex.
m9_has() { echo "$1" | tr ',' '\n' | grep -Eq "$2" && echo true || echo false; }

# m9_received <mock> <method>: how many messages of method the mock received.
m9_received() { scn_mcp_received "$1" 0 "$2" | jq '.received | length'; }

m9_up

scn_check "1. orgs A and B push a server named github, each on its own mock"
scn_org_config "$M9_A" "{\"mcp\":{\"enabled\":true,\"servers\":{\"github\":{\"url\":\"$SCN_MCP_A_URL\",
  \"credential\":{\"mode\":\"shared\",\"credential_id\":\"cred_m9_a\",\"secret\":{\"type\":\"bearer\",\"value\":\"$M9_A_TOKEN\"}}}}}}"
# B allows its compatibility view (M10: a config with no mcp.access defaults to
# deny, which lists nothing; M14's migration sets allow for existing orgs).
scn_org_config "$M9_B" "{\"mcp\":{\"enabled\":true,\"access\":{\"default\":\"allow\"},\"servers\":{\"github\":{\"url\":\"$SCN_MCP_B_URL\"},
  \"late\":{\"url\":\"http://127.0.0.1:$M9_LATE_PORT\"}}}}"

scn_check "2. A's status: one server, github, healthy; every admin call names a known org"
scn_assert_eq 2.status 200 "$(scn_admin GET "/-/mcp/status?org_id=$M9_A")"
scn_assert_jq 2.org_enabled '.org_enabled' true
scn_assert_jq 2.one_server '.servers | length' 1
scn_assert_jq 2.github_healthy '.servers[0] | "\(.server_id) \(.healthy) \(.tool_count) \(.last_error)"' "github true 2 null"
scn_assert_jq 2.tool_total '.tools' 2
# M13's client detects the dual-era mock as 2026-07-28 (server/discover, no
# initialize), so the bearer is read from the last message of any method.
scn_assert_eq 2.a_saw_its_bearer "$(sha256 "Bearer $M9_A_TOKEN")" \
  "$(scn_mcp_received a 1 | jq -r '.received[0].auth.sha256 // ""')"
for path in /-/mcp/status /-/mcp/tools /-/mcp/resources /-/mcp/prompts; do
  scn_assert_eq "2.no_org_id$path" 400 "$(scn_admin GET "$path")"
  scn_assert_eq "2.unknown_org$path" 404 "$(scn_admin GET "$path?org_id=org-m9-nobody")"
done
scn_assert_eq 2.test_no_org_id 400 "$(scn_admin POST /-/mcp/test '{"name":"github_a_search_issues","arguments":{}}')"
scn_assert_eq 2.test_unknown_org 404 "$(scn_admin POST /-/mcp/test '{"org_id":"org-m9-nobody","name":"x","arguments":{}}')"
scn_assert_eq 2.no_admin_token 401 "$(SCN_ADMIN_TOKEN=wrong scn_admin GET "/-/mcp/status?org_id=$M9_A")"

scn_check "3. each org lists only its own tools; the YAML server serves only the static org"
# Orgs served through virtual servers name their tools <server>__<tool> (M10's
# prefixed naming, adminToolName since the W3b merge); the static org without
# an mcp section keeps the legacy view's <server>_<tool>.
scn_assert_eq 3.a_tools "github:github__a_open_issue,github:github__a_search_issues" "$(m9_tools "$M9_A")"
scn_assert_eq 3.b_only_its_own "true" "$(m9_tools "$M9_B" | python3 -I -c 'import sys
names = sys.stdin.read().strip().split(",")
print(str("github:github__b_delete_repo" in names and not any("_a_" in n or "ops_" in n for n in names)).lower())')"
scn_assert_eq 3.static_org "ops:ops_ops_restart" "$(m9_tools "$M9_OPS")"
scn_assert_eq 3.static_status 200 "$(scn_admin GET "/-/mcp/status?org_id=$M9_OPS")"
scn_assert_jq 3.static_healthy '[.servers[] | "\(.server_id) \(.healthy)"] | join(",")' "ops true"

scn_check "4. a server down at push shows last_error, then is healthy within 10 s of its mock starting"
scn_assert_eq 4.status 200 "$(scn_admin GET "/-/mcp/status?org_id=$M9_B")"
scn_assert_jq 4.github_healthy '[.servers[] | select(.server_id == "github") | .healthy] | tostring' "[true]"
scn_assert_jq 4.late_down '[.servers[] | select(.server_id == "late") | (.healthy == false and (.last_error // "") != "")] | tostring' "[true]"
m9_start_late
started=$(date +%s)
healthy=false
while [ $(( $(date +%s) - started )) -le 10 ]; do
  scn_admin GET "/-/mcp/status?org_id=$M9_B" >/dev/null
  if [ "$(jq -r '[.servers[] | select(.server_id == "late") | .healthy] | tostring' <"$SCN_DIR/last.body")" = "[true]" ]; then
    healthy=true
    break
  fi
  sleep 0.5
done
scn_assert_eq 4.late_healthy_within_10s true "$healthy"
scn_assert_jq 4.late_error_cleared '[.servers[] | select(.server_id == "late") | .last_error] | tostring' "[null]"

scn_check "5. a policy-only push reconnects nothing"
inits=$(m9_received a initialize)
scn_org_config "$M9_A" "{\"mcp\":{\"enabled\":true,\"access\":{\"default\":\"allow\"},\"servers\":{\"github\":{\"url\":\"$SCN_MCP_A_URL\",
  \"credential\":{\"mode\":\"shared\",\"credential_id\":\"cred_m9_a\",\"secret\":{\"type\":\"bearer\",\"value\":\"$M9_A_TOKEN\"}}}}}}"
scn_assert_eq 5.status 200 "$(scn_admin GET "/-/mcp/status?org_id=$M9_A")"
sleep 1 # a rebuild would run in the background
scn_assert_eq 5.same_connection "$inits" "$(m9_received a initialize)"
scn_assert_jq 5.still_healthy '.servers[0].healthy' true

scn_check "6. no admin GET shows a secret"
scn_assert_eq 6.config_status 200 "$(scn_admin GET "/-/orgs/$M9_A/config")"
scn_assert_eq 6.no_token_in_config no "$(grep -qF "$M9_A_TOKEN" "$SCN_DIR/last.body" && echo yes || echo no)"
scn_assert_jq 6.secret_masked '.mcp.servers.github.credential.secret.value' '****'
scn_assert_jq 6.credential_id_kept '.mcp.servers.github.credential.credential_id' cred_m9_a
scn_assert_eq 6.list_status 200 "$(scn_admin GET /-/orgs/configs)"
scn_assert_eq 6.no_token_in_list no "$(grep -qF "$M9_A_TOKEN" "$SCN_DIR/last.body" && echo yes || echo no)"
for path in status tools resources prompts; do
  scn_admin GET "/-/mcp/$path?org_id=$M9_A" >/dev/null
  scn_assert_eq "6.no_token_in_$path" no "$(grep -qF "$M9_A_TOKEN" "$SCN_DIR/last.body" && echo yes || echo no)"
done

if [ "${SCN_WAVE_GATE:-}" = 1 ]; then
  scn_check "7. (wave gate) keys see and call only their own org's tools"
  a_calls=$(m9_received a tools/call)
  b_calls=$(m9_received b tools/call)
  SCN_KEY=$M9_A_KEY scn_mcp_session_init
  scn_assert_eq 7.a_list 200 "$(SCN_KEY=$M9_A_KEY scn_mcp_post /mcp '{"jsonrpc":"2.0","id":2,"method":"tools/list"}')"
  a_listed=$(jq -r '[.result.tools[].name] | join(",")' <"$SCN_DIR/last.body")
  scn_assert_eq 7.a_lists_own true "$(m9_has "$a_listed" 'a_search_issues')"
  scn_assert_eq 7.a_lists_no_other false "$(m9_has "$a_listed" 'b_delete_repo|ops_restart')"
  a_tool=$(jq -r '[.result.tools[].name | select(test("a_search_issues"))][0]' <"$SCN_DIR/last.body")
  # The bodies are built outside "$(...)": macOS bash 3.2 brace-expands a
  # double-quoted JSON object nested in a quoted command substitution.
  a_body="{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"$a_tool\",\"arguments\":{}}}"
  scn_assert_eq 7.a_call 200 "$(SCN_KEY=$M9_A_KEY scn_mcp_post /mcp "$a_body")"
  scn_assert_eq 7.a_call_on_a "$((a_calls + 1))" "$(m9_received a tools/call)"
  scn_assert_eq 7.a_call_not_on_b "$b_calls" "$(m9_received b tools/call)"
  unset SCN_MCP_SESSION
  SCN_KEY=$M9_B_KEY scn_mcp_session_init
  scn_assert_eq 7.b_list 200 "$(SCN_KEY=$M9_B_KEY scn_mcp_post /mcp '{"jsonrpc":"2.0","id":2,"method":"tools/list"}')"
  b_listed=$(jq -r '[.result.tools[].name] | join(",")' <"$SCN_DIR/last.body")
  scn_assert_eq 7.b_lists_own true "$(m9_has "$b_listed" 'b_delete_repo')"
  scn_assert_eq 7.b_lists_no_other false "$(m9_has "$b_listed" 'a_search_issues|ops_restart')"
  unset SCN_MCP_SESSION
  a_tool=$(m9_tools "$M9_A" | tr ',' '\n' | sed -n 's/^github://p' | grep a_search_issues | head -1)
  test_body="{\"org_id\":\"$M9_A\",\"name\":\"$a_tool\",\"arguments\":{}}"
  scn_assert_eq 7.admin_test 200 "$(scn_admin POST /-/mcp/test "$test_body")"
  scn_assert_jq 7.admin_test_ok '(.error // "") + "|" + (.is_error | tostring)' "|false"
  scn_assert_eq 7.admin_test_on_a "$((a_calls + 2))" "$(m9_received a tools/call)"
  scn_assert_eq 7.admin_test_not_on_b "$b_calls" "$(m9_received b tools/call)"
else
  echo
  echo "=== 7. skipped: the key-isolation and call steps run at the W3b gate (SCN_WAVE_GATE=1), after M8, M10 and M13 merge"
fi

scn_pass
