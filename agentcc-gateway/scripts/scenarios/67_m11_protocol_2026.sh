#!/usr/bin/env bash
# M11: 2026-07-28 stateless serving and the shared Streamable HTTP rules,
# through the real gateway binary in front of agentcc-mcpmock.
#  1. A 2026 client (per-request _meta, mirrored headers, no session)
#     discovers the gateway: server/discover is a complete, private result
#     with the gateway's serverInfo (in _meta["io.modelcontextprotocol/
#     serverInfo"]), the revisions served and no listChanged promise.
#  2. The 2026 client lists the tools (a complete, private list with ttlMs)
#     and calls the fixture tool: its structuredContent, its resource_link
#     (every optional field), its _meta and an unknown extension member
#     arrive intact, and the result is complete; the io.futureagi/* _meta
#     keys the upstream forged (an error object, a billing record) do not.
#  3. A mismatched Mcp-Name is HTTP 400 with -32020 before the mock sees
#     anything, and so is a body that names the tool twice in two letter
#     cases (the header matched one spelling and the gateway ran the other);
#     an unknown method is HTTP 404 with -32601 on a 2026 request, and an
#     unknown tool is not an unknown method.
#  4. A 10 MiB body is 413, and so is a 2 MiB one under the configured 1 MiB
#     cap (mcp.max_body_bytes reaches the server); a small body is served.
#  5. Origin https://evil.example is 403; the configured allowed origin is
#     served (mcp.allowed_origins reaches the server), and so is the
#     gateway's own loopback origin; a DNS-rebinding request (its own name as
#     both Host and Origin) is 403.
#  6. A legacy (2025-06-18) client on the same endpoint still works, with the
#     same raw result and no 2026 fields; server/discover, a 2026 method, is
#     an unknown method to it.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<lane db> bash scripts/scenarios/67_m11_protocol_2026.sh
#
# The mock runs --era dual: the gateway's upstream client at this PIN still
# initializes upstream as a legacy client (M13 adds era detection), so what
# this scenario proves is the downstream half, a 2026 client and 2026-shaped
# results through the gateway. The W3b gate reruns it on the merged tree,
# where M10 names the tools (the script finds the fixture tool by suffix).
set -u
source "$(dirname "$0")/lib.sh"
source "$(dirname "$0")/lib_mcp.sh"

TEMPLATE=$SCN_ROOT/fixtures/M11/gateway.yaml
FIXTURES=$SCN_ROOT/fixtures/M11/mcp
V2026=2026-07-28

# m11_body <id> <method> <params json>: the JSON-RPC body of a 2026 request,
# its params carrying the per-request _meta.
m11_body() {
  python3 -I - "$1" "$2" "$3" <<'PY'
import json, sys
rid, method, params = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
params["_meta"] = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "scn-m11", "version": "1"},
}
print(json.dumps({"jsonrpc": "2.0", "id": int(rid), "method": method, "params": params}))
PY
}

# m11_post <id> <method> <params json> [header...]: POST a 2026 request with
# the mirrored headers a compliant client sends (no session); extra headers
# follow them. Prints the HTTP status.
m11_post() {
  local id=$1 method=$2 params=$3 name
  shift 3
  name=$(python3 -I -c 'import json,sys; p=json.loads(sys.argv[1]); print(p.get("name") or p.get("uri") or "")' "$params")
  local hdrs=("MCP-Protocol-Version: $V2026" "Mcp-Method: $method")
  [ -n "$name" ] && hdrs+=("Mcp-Name: $name")
  SCN_MCP_SESSION="" scn_mcp_post /mcp "$(m11_body "$id" "$method" "$params")" "${hdrs[@]}" "$@"
}

# m11_raw <file> [header...]: POST a body file as is (large bodies). Prints
# the HTTP status; a connection the gateway closes mid-upload still reports
# the status it sent. Under load, curl can lose that race: the gateway's 413
# arrives while curl is still uploading, and curl records only the interim
# "100 Continue" (or no status at all). Such a read is no answer, so it is
# tried once more; any final status counts, and two lost reads still fail.
m11_raw() {
  local file=$1 h args=() code try
  shift
  for h in "$@"; do args+=(-H "$h"); done
  for try in 1 2; do
    code=$(curl -sS --max-time 60 -o "$SCN_DIR/last.body" -D "$SCN_DIR/last.headers" -w '%{http_code}' -X POST "$SCN_GW/mcp" \
      -H "Authorization: Bearer $SCN_KEY" -H 'Content-Type: application/json' \
      -H 'Accept: application/json, text/event-stream' ${args[@]+"${args[@]}"} --data-binary "@$file" 2>/dev/null)
    case $code in 100 | 000 | "") continue ;; esac
    break
  done
  echo "$code"
  return 0
}

# m11_pad_file <name> <bytes>: a ping whose params pad the body to <bytes>.
m11_pad_file() {
  python3 -I - "$SCN_DIR/$1" "$2" <<'PY'
import sys
path, size = sys.argv[1], int(sys.argv[2])
head, tail = '{"jsonrpc":"2.0","id":1,"method":"ping","params":{"pad":"', '"}}'
with open(path, "w") as f:
    f.write(head + "x" * max(0, size - len(head) - len(tail)) + tail)
PY
  echo "$SCN_DIR/$1"
}

calls_seen() { scn_mcp_received main 0 tools/call | jq '.received | length'; }

# same <jq filter> <file>: the filtered value of file in canonical JSON (keys
# sorted), so a comparison ignores member order, which JSON does not fix.
same() { jq -cS "$1" "$2"; }

# UPSTREAM_META: a result's _meta without the keys that are not the
# upstream's to set: the gateway's io.futureagi/* namespace and the 2026
# serverInfo the gateway answers with.
UPSTREAM_META='._meta | with_entries(select((.key | startswith("io.futureagi/") | not) and .key != "io.modelcontextprotocol/serverInfo"))'
# FORGED: how many io.futureagi/* values of a result's _meta are the
# upstream's forgeries (the fixture marks them forged: true).
FORGED='[.result._meta // {} | to_entries[] | select(.key | startswith("io.futureagi/")) | select((.value | type) == "object" and .value.forged == true)] | length'

scn_mcp_up main --era dual --fixtures "$FIXTURES"
scn_mcp_gateway_template "$TEMPLATE"
SCN_CATALOG_WAIT_SKIP=all scn_up # no managed provider in the template

scn_check "1. a 2026 client discovers the gateway"
scn_assert_eq 1.discover_status 200 "$(m11_post 1 server/discover '{}')"
scn_assert_jq 1.result_type '.result.resultType' complete
scn_assert_jq 1.cache_scope '.result.cacheScope' private
scn_assert_jq 1.server_info '.result._meta["io.modelcontextprotocol/serverInfo"].name' "Future AGI gateway"
scn_assert_jq 1.no_list_changed_promise '.result.capabilities.tools.listChanged // false' false
scn_assert_jq 1.supports_2026 '.result.supportedVersions | index("2026-07-28") != null' true
scn_assert_jq 1.ttl '.result.ttlMs > 0' true
scn_assert_eq 1.no_session_issued "" "$(scn_hdr Mcp-Session-Id)"

scn_check "2. the 2026 client lists and calls the tool; structuredContent and the resource_link arrive intact"
scn_assert_eq 2.list_status 200 "$(m11_post 2 tools/list '{}')"
scn_assert_jq 2.list_result_type '.result.resultType' complete
scn_assert_jq 2.list_cache_scope '.result.cacheScope' private
scn_assert_jq 2.list_ttl '.result.ttlMs > 0' true
TOOL=$(jq -r '[.result.tools[].name | select(endswith("report"))][0] // ""' "$SCN_DIR/last.body")
[ -n "$TOOL" ] || scn_fail 2.tool_listed "no *report tool in $(cat "$SCN_DIR/last.body")"
# The call's params, built once (bash 3.2 brace-expands nested braces in
# "$(... "{...}")").
CALL_PARAMS=$(jq -nc --arg t "$TOOL" '{name: $t, arguments: {city: "Paris"}}')
before=$(calls_seen)
scn_assert_eq 2.call_status 200 "$(m11_post 3 tools/call "$CALL_PARAMS")"
cp "$SCN_DIR/last.body" "$SCN_DIR/call-2026.json"
scn_assert_jq 2.result_type '.result.resultType' complete
WANT=$FIXTURES/tools.json
CALL=$SCN_DIR/call-2026.json
scn_assert_eq 2.structured_content "$(same '.[0].result.structuredContent' "$WANT")" "$(same '.result.structuredContent' "$CALL")"
scn_assert_eq 2.resource_link "$(same '.[0].result.content[1]' "$WANT")" "$(same '.result.content[1]' "$CALL")"
scn_assert_eq 2.content "$(same '.[0].result.content' "$WANT")" "$(same '.result.content' "$CALL")"
scn_assert_eq 2.upstream_meta "$(same ".[0].result | $UPSTREAM_META" "$WANT")" "$(same ".result | $UPSTREAM_META" "$CALL")"
scn_assert_eq 2.forged_gateway_meta_dropped 0 "$(jq "$FORGED" "$CALL")"
scn_assert_eq 2.extension_member "$(same '.[0].result["x-apps"]' "$WANT")" "$(same '.result["x-apps"]' "$CALL")"
scn_assert_eq 2.mock_called $((before + 1)) "$(calls_seen)"

scn_check "3. a mismatched Mcp-Name is 400 -32020 before the mock sees anything; unknown methods are 404 on 2026"
before=$(calls_seen)
scn_assert_eq 3.mismatch_status 400 \
  "$(SCN_MCP_SESSION="" scn_mcp_post /mcp "$(m11_body 4 tools/call "$CALL_PARAMS")" \
    "MCP-Protocol-Version: $V2026" "Mcp-Method: tools/call" "Mcp-Name: other_tool")"
scn_assert_jq 3.mismatch_code '.error.code' -32020
scn_assert_eq 3.mock_not_called "$before" "$(calls_seen)"
# Mcp-Name: other_tool equals the exact "name"; the case-insensitive decoder
# the gateway runs calls with reads "NAME", the real tool (review W3b-r3 P1).
CASE_PARAMS=$(jq -nc --arg t "$TOOL" '{name: "other_tool", NAME: $t, arguments: {city: "Paris"}}')
scn_assert_eq 3.case_variant_name_status 400 "$(m11_post 10 tools/call "$CASE_PARAMS")"
scn_assert_jq 3.case_variant_name_code '.error.code' -32020
scn_assert_eq 3.case_variant_mock_not_called "$before" "$(calls_seen)"
NOSUCH_PARAMS='{"name":"no_such_tool","arguments":{}}'
nosuch_status=$(m11_post 11 tools/call "$NOSUCH_PARAMS")
scn_assert_eq 3.unknown_tool_not_404 "not 404" "$([ "$nosuch_status" = 404 ] && echo 404 || echo 'not 404')"
scn_assert_eq 3.unknown_method_status 404 "$(m11_post 5 no/such/method '{}')"
scn_assert_jq 3.unknown_method_code '.error.code' -32601
scn_assert_eq 3.ping_removed_in_2026 404 "$(m11_post 6 ping '{}')"

scn_check "4. body caps: 10 MiB and 2 MiB are 413 under the configured 1 MiB; a small body is served"
scn_assert_eq 4.ten_mib 413 "$(m11_raw "$(m11_pad_file big10.json $((10 * 1024 * 1024)))")"
scn_assert_eq 4.two_mib_over_configured_cap 413 "$(m11_raw "$(m11_pad_file big2.json $((2 * 1024 * 1024)))")"
scn_assert_eq 4.small 200 "$(m11_raw "$(m11_pad_file small.json 1024)")"

scn_check "5. Origin: evil and DNS rebinding are 403; the configured and same loopback origins are served"
scn_assert_eq 5.evil_origin 403 "$(m11_post 7 tools/list '{}' 'Origin: https://evil.example')"
scn_assert_jq 5.refusal_code '.error.data.code' origin_not_allowed
scn_assert_eq 5.allowed_origin 200 "$(m11_post 8 tools/list '{}' 'Origin: https://app.example')"
GW_HOSTPORT=${SCN_GW#http://}
scn_assert_eq 5.same_origin_loopback 200 "$(m11_post 8 tools/list '{}' "Origin: $SCN_GW")"
REBIND=rebind.example:${GW_HOSTPORT##*:}
scn_assert_eq 5.dns_rebinding 403 "$(m11_post 8 tools/list '{}' "Host: $REBIND" "Origin: http://$REBIND")"

scn_check "6. a legacy client on the same endpoint gets the same raw result and no 2026 fields"
scn_mcp_session_init 2025-06-18
scn_assert_eq 6.legacy_call 200 \
  "$(scn_mcp_post /mcp "$(jq -nc --argjson p "$CALL_PARAMS" '{jsonrpc: "2.0", id: 9, method: "tools/call", params: $p}')")"
scn_assert_eq 6.legacy_structured "$(same '.[0].result.structuredContent' "$WANT")" "$(same '.result.structuredContent' "$SCN_DIR/last.body")"
scn_assert_eq 6.legacy_resource_link "$(same '.[0].result.content[1]' "$WANT")" "$(same '.result.content[1]' "$SCN_DIR/last.body")"
scn_assert_jq 6.legacy_no_result_type '.result.resultType // "absent"' absent
scn_assert_eq 6.legacy_forged_gateway_meta_dropped 0 "$(jq "$FORGED" "$SCN_DIR/last.body")"
scn_assert_eq 6.legacy_upstream_meta "$(same ".[0].result | $UPSTREAM_META" "$WANT")" "$(same ".result | $UPSTREAM_META" "$SCN_DIR/last.body")"
# Read last: the discover answer replaces last.body.
scn_assert_eq 6.legacy_discover_status 200 "$(scn_mcp_post /mcp '{"jsonrpc":"2.0","id":12,"method":"server/discover"}')"
scn_assert_jq 6.legacy_discover_unknown '.error.code' -32601

scn_pass
