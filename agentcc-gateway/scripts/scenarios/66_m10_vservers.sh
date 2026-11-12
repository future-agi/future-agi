#!/usr/bin/env bash
# Virtual servers, collision-free names and access rules, behind the real
# gateway, against two agentcc-mcpmock upstreams: git_hub (tools search and
# delete_repo) and git (tools hub_search and other), bug 13's pair.
#
# Every run (the legacy view: org-acme's YAML key, no org config):
#  1. The YAML pair collides under "_": git_hub_search is listed once, and a
#     call on it lands once on one mock only, under that server's own tool
#     name (search or hub_search); nothing is overwritten.
#  2. 20 consecutive tools/list calls return byte-identical, sorted bodies.
#  3. {endpoint}/v/{vs_id} serves a virtual server only in the caller's org:
#     org-acme has none, so it is 404; /mcp?vs_id= selects nothing.
#
# With SCN_WAVE_GATE=1 (the wave gate, once the key's principal and the org pools
# are merged), the virtual-server steps against org-m10's pushed config:
#  4. Key K1, bound to vs-a, sees on /mcp only vs-a's included tools, and both
#     of bug 13's pair, under distinct names (git_hub__search, git__hub_search).
#  5. 20 consecutive tools/list calls through vs-a are byte-identical.
#  6. Calling a hidden tool (git__other, not included) returns -32602; calling
#     a visible tool an access rule denies K1 (git_hub__delete_repo) returns
#     isError with _meta["io.futureagi/error"].code = "tool_denied".
#  7. A call reaches the right mock under its upstream name; /mcp/v/{vs_id}
#     serves vs-a to K1 and is 404 to K2, a key vs-a's access does not name.
#  8. Key K3 of an org with no MCP config (not mcp.static.org_id) lists none
#     of the operator's YAML servers, and calling one of their tools is an
#     unknown tool that reaches no mock: the legacy view is the static org's.
#  9. With mcp.static.org_id set, the YAML key that names no org (a principal
#     with no org) is served an empty pool too: it lists none of the YAML
#     servers, and calling one of their tools reaches no mock (no org is not
#     the static org). With static.org_id unset such a principal
#     keeps the legacy view (TestOrgWithoutMCPConfigSeesNoStaticTools).
#
# static.org_id is rendered in every run, not only under the gate: the gateway binds
# the YAML key to org-acme, so without it steps 1-3 would list nothing.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<lane db> bash scripts/scenarios/66_m10_vservers.sh
#   SCN_WAVE_GATE=1 ... (at the wave gate)
set -u
source "$(dirname "$0")/lib.sh"
source "$(dirname "$0")/lib_mcp.sh"

TEMPLATE=$SCN_ROOT/fixtures/M10/gateway.yaml
M10_ORG="org-m10"
M10_ORG_NOMCP="org-m10-nomcp" # never given an org config
VS_A=vs_01JM10SCENAR10VSA000000000 # a fixed, valid vs_<ULID>, as the config store would assign
LIST='{"jsonrpc":"2.0","id":2,"method":"tools/list"}'

m10_call() { # name: a tools/call body
  echo "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"$1\",\"arguments\":{\"q\":\"x\"}}}"
}

# m10_lists <step> <path> [header...]: 20 tools/list calls must be byte for
# byte the same and sorted by name; the first body is kept as <step>.json.
m10_lists() {
  local step=$1 path=$2 i code first
  shift 2
  for i in $(seq 1 20); do
    code=$(scn_mcp_post "$path" "$LIST" "$@")
    [ "$code" = 200 ] || scn_fail "$step.list_$i" "tools/list returned HTTP $code: $(cat "$SCN_DIR/last.body")"
    if [ "$i" = 1 ]; then
      first=$(cat "$SCN_DIR/last.body")
      cp "$SCN_DIR/last.body" "$SCN_DIR/$step.json"
    elif [ "$(cat "$SCN_DIR/last.body")" != "$first" ]; then
      scn_fail "$step.byte_identical" "tools/list #$i differs: $(cat "$SCN_DIR/last.body") vs $first"
    fi
  done
  scn_assert_eq "$step.twenty_identical" 20 "$i"
  scn_assert_jq "$step.sorted" '[.result.tools[].name] as $n | $n == ($n | sort)' true "$SCN_DIR/$step.json"
}

# --- start: two mocks, then the gateway with their ports in the allowlist ---
mkdir -p "$SCN_BIN"
scn_mcp_up git_hub --fixtures "$SCN_ROOT/fixtures/M10/git_hub"
scn_mcp_up git --fixtures "$SCN_ROOT/fixtures/M10/git"
SRC=$SCN_BIN/m10-gateway.yaml
sed -e "s/@@M10_GIT_HUB_PORT@@/$SCN_MCP_GIT_HUB_PORT/" -e "s/@@M10_GIT_PORT@@/$SCN_MCP_GIT_PORT/" "$TEMPLATE" >"$SRC"
grep -q '@@M10_' "$SRC" && scn_fail m10_up "unrendered placeholders in $SRC"
scn_mcp_gateway_template "$SRC"
SCN_CATALOG_WAIT_SKIP=all scn_up # no model provider: this scenario makes no model call

scn_check "1. the legacy view: bug 13's pair is never overwritten"
scn_mcp_session_init
code=$(scn_mcp_post /mcp "$LIST")
scn_assert_eq 1.status 200 "$code"
cp "$SCN_DIR/last.body" "$SCN_DIR/legacy-list.json"
scn_assert_jq 1.names '[.result.tools[].name] | join(",")' 'git_hub_delete_repo,git_hub_search,git_other' "$SCN_DIR/legacy-list.json"
code=$(scn_mcp_post /mcp "$(m10_call git_hub_search)")
scn_assert_eq 1.call_status 200 "$code"
scn_assert_jq 1.call_ok '.error // empty | .message' ""
# The holder is read from the mocks' call logs, not an admin listing (with org pools
# on, /-/mcp/tools is per org and names each server's tools on its own):
# exactly one mock got the call, once, under its own upstream name.
HUB_CALLS=$(scn_mcp_received git_hub 0 tools/call | jq '.received | length')
GIT_CALLS=$(scn_mcp_received git 0 tools/call | jq '.received | length')
case "$HUB_CALLS,$GIT_CALLS" in
  1,0) HOLDER=git_hub HOLDER_TOOL=search OTHER=git ;;
  0,1) HOLDER=git HOLDER_TOOL=hub_search OTHER=git_hub ;;
  *) scn_fail 1.called_once "git_hub_search reached git_hub ${HUB_CALLS:-?} times and git ${GIT_CALLS:-?} times, want one of them once" ;;
esac
scn_assert_eq 1.called_once 1 "$((HUB_CALLS + GIT_CALLS))"
scn_mcp_received "$HOLDER" 0 tools/call >"$SCN_DIR/holder-calls.json"
scn_assert_jq 1.holder_called "[.received[] | .name] | join(\",\")" "$HOLDER_TOOL" "$SCN_DIR/holder-calls.json"
scn_mcp_received "$OTHER" 0 tools/call >"$SCN_DIR/other-calls.json"
scn_assert_jq 1.other_not_called '.received | length' 0 "$SCN_DIR/other-calls.json"

scn_check "2. 20 consecutive legacy tools/list calls are byte-identical and sorted"
m10_lists 2 /mcp

scn_check "3. /mcp/v/{vs_id} serves only the caller's org's virtual servers"
# The gateway binds a legacy session to the path it was opened on, so the vs path gets
# its own session (initialize is not chained: it opens one on any vs_id), and
# the 404 is the chain's virtual_server_not_found, not an unknown session.
endpoint_session=$SCN_MCP_SESSION
scn_mcp_session_init 2025-06-18 "/mcp/v/$VS_A"
code=$(scn_mcp_post "/mcp/v/$VS_A" "$LIST")
scn_assert_eq 3.no_org_config_404 404 "$code"
scn_assert_jq 3.body '.error.message' 'virtual server not found'
SCN_MCP_SESSION=$endpoint_session
code=$(scn_mcp_post "/mcp?vs_id=$VS_A" "$LIST")
scn_assert_eq 3.query_selects_nothing 200 "$code"
scn_assert_eq 3.query_same_list "$(cat "$SCN_DIR/legacy-list.json")" "$(cat "$SCN_DIR/last.body")"

if [ "${SCN_WAVE_GATE:-}" != 1 ]; then
  echo
  echo "Steps 4-8 need the key's principal and the org pools: they run at the wave gate (SCN_WAVE_GATE=1)."
  scn_pass
  exit 0
fi

scn_check "4. K1, bound to vs-a, sees only vs-a's included tools, the pair under distinct names"
K1=$(scn_key "$M10_ORG")
K1_ID=$(jq -r .id "$SCN_DIR/last.body")
K2=$(scn_key "$M10_ORG")
if [ -z "$K1_ID" ] || [ "$K1_ID" = null ]; then
  scn_fail 4.k1_id "no key id: $(cat "$SCN_DIR/last.body")"
fi
# The org's servers name the mocks' path: the upstream client posts to the URL exactly.
python3 -I - "$K1_ID" "$VS_A" "$SCN_MCP_GIT_HUB_URL/mcp" "$SCN_MCP_GIT_URL/mcp" >"$SCN_DIR/org-m10.json" <<'PY'
import json, re, sys
k1, vs, hub, git = sys.argv[1:5]
assert re.fullmatch(r"vs_[0-9A-HJKMNP-TV-Z]{26}", vs), vs
print(json.dumps({"mcp": {
    "enabled": True,
    "access": {"default": "deny"},
    "servers": {"git_hub": {"url": hub, "transport": "http"}, "git": {"url": git, "transport": "http"}},
    "virtual_servers": {"vs-a": {
        "id": vs,
        "members": ["git_hub", "git"],
        "tools": {"include": ["git_hub/*", "git/hub_search"]},
        "access": {"keys": [k1], "rules": [{"match": {"key_ids": [k1]}, "deny": ["git_hub/delete_repo"]}]},
    }},
}}))
PY
scn_org_config "$M10_ORG" "$(cat "$SCN_DIR/org-m10.json")"
SCN_KEY=$K1
deadline=$(( $(date +%s) + 20 )) # the org's pool connects in the background
while :; do
  SCN_MCP_SESSION=""
  scn_mcp_session_init
  code=$(scn_mcp_post /mcp "$LIST")
  [ "$code" = 200 ] && [ "$(jq -r '[.result.tools[]?.name] | length' "$SCN_DIR/last.body")" = 2 ] && break
  [ "$(date +%s)" -ge "$deadline" ] && scn_fail 4.pool "vs-a never listed its tools: HTTP $code $(cat "$SCN_DIR/last.body")"
  sleep 0.5
done
scn_assert_jq 4.names '[.result.tools[].name] | join(",")' 'git__hub_search,git_hub__search'

scn_check "5. 20 consecutive tools/list calls through vs-a are byte-identical"
m10_lists 5 /mcp
scn_assert_jq 5.names '[.result.tools[].name] | join(",")' 'git__hub_search,git_hub__search' "$SCN_DIR/5.json"

scn_check "6. a hidden tool is unknown; a visible one the access rule denies is tool_denied"
code=$(scn_mcp_post /mcp "$(m10_call git__other)")
scn_assert_eq 6.hidden_status 200 "$code"
scn_assert_jq 6.hidden_code '.error.code' -32602
scn_assert_jq 6.hidden_message '.error.message' 'Unknown tool: git__other'
code=$(scn_mcp_post /mcp "$(m10_call git_hub__delete_repo)")
scn_assert_eq 6.denied_status 200 "$code"
scn_assert_jq 6.denied_is_error '.result.isError' true
scn_assert_jq 6.denied_code '.result._meta["io.futureagi/error"].code' tool_denied
scn_assert_jq 6.denied_rule '.result._meta["io.futureagi/error"].metadata.rule' 'access.rules[0]'
scn_assert_jq 6.denied_vs '.result._meta["io.futureagi/error"].metadata.virtual_server' "$VS_A"

scn_check "7. calls reach the right mock; /mcp/v/{vs_id} is vs-a for K1 and 404 for K2"
code=$(scn_mcp_post /mcp "$(m10_call git__hub_search)")
scn_assert_eq 7.call_status 200 "$code"
scn_mcp_received git 1 tools/call >"$SCN_DIR/git-call.json"
scn_assert_jq 7.git_got_hub_search '.received[0].name' hub_search "$SCN_DIR/git-call.json"
SCN_MCP_SESSION=""
scn_mcp_session_init 2025-06-18 "/mcp/v/$VS_A"
code=$(scn_mcp_post "/mcp/v/$VS_A" "$LIST")
scn_assert_eq 7.vs_path_k1 200 "$code"
scn_assert_jq 7.vs_path_names '[.result.tools[].name] | join(",")' 'git__hub_search,git_hub__search'
SCN_KEY=$K2
SCN_MCP_SESSION=""
scn_mcp_session_init 2025-06-18 "/mcp/v/$VS_A" # a session on the vs path, so the 404 is vs-a's access
code=$(scn_mcp_post "/mcp/v/$VS_A" "$LIST")
scn_assert_eq 7.vs_path_k2_404 404 "$code"
scn_assert_jq 7.vs_path_k2_body '.error.message' 'virtual server not found'

scn_check "8. a key of an org with no MCP config sees none of the static servers"
m10_calls() { # the tools/call count both mocks have received
  echo $(( $(scn_mcp_received git_hub 0 tools/call | jq '.received | length') + $(scn_mcp_received git 0 tools/call | jq '.received | length') ))
}
SCN_KEY=$(scn_key "$M10_ORG_NOMCP")
SCN_MCP_SESSION=""
scn_mcp_session_init
code=$(scn_mcp_post /mcp "$LIST")
scn_assert_eq 8.list_status 200 "$code"
scn_assert_jq 8.lists_nothing '[.result.tools[]?.name] | length' 0
before=$(m10_calls)
code=$(scn_mcp_post /mcp "$(m10_call git_hub_search)")
scn_assert_eq 8.call_status 200 "$code"
scn_assert_jq 8.unknown_code '.error.code' -32602
scn_assert_jq 8.unknown_message '.error.message' 'Unknown tool: git_hub_search'
scn_assert_eq 8.no_mock_called "$before" "$(m10_calls)"

scn_check "9. with static.org_id set, a principal with no org sees none of the static servers"
SCN_KEY=scn-test-key-m10-orgless # the fixture's YAML key with no org_id
SCN_MCP_SESSION=""
scn_mcp_session_init
code=$(scn_mcp_post /mcp "$LIST")
scn_assert_eq 9.list_status 200 "$code"
scn_assert_jq 9.lists_nothing '[.result.tools[]?.name] | length' 0
before=$(m10_calls)
code=$(scn_mcp_post /mcp "$(m10_call git_hub_search)")
scn_assert_eq 9.call_status 200 "$code"
scn_assert_jq 9.unknown_code '.error.code' -32602
scn_assert_jq 9.unknown_message '.error.message' 'Unknown tool: git_hub_search'
scn_assert_eq 9.no_mock_called "$before" "$(m10_calls)"

scn_pass
