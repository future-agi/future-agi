#!/usr/bin/env bash
# Gate check: MCP
# isolation through the real gateway binary, two agentcc-mcpmock upstreams,
# the mock as the log webhook, and Django on the gate database.
#  1. Org A's config is written in Django with a shared credential stored in
#     the vault: the push assembly gives the org's pool the secret (A's mock saw
#     A's bearer, github healthy), and the config redaction hides it from
#     GET /-/orgs/{A}/config. Org B's config is pushed by the admin API.
#  2. Both orgs have a server named github, each on its own mock: each key
#     lists only its org's tools (never the other org's, never the operator's
#     YAML server), and each call lands only on its own mock (/__received).
#  3. Org A's legacy session id is 404 for org B's key; anonymous POST, GET
#     and DELETE on /mcp are 401; one 2026-07-28 request and one legacy
#     session on the same endpoint both succeed.
#  4. The admin playground (POST /-/mcp/test for A) meters exactly one
#     tool_call record, principal_kind admin, and B probes a hidden
#     tool with a 300-character name.
#  5. The captured tool_call events of both orgs, one webhook flush, ingest
#     into Django under the right org through the webhook's own entry point:
#     every row stored, the probe under its name cut to 255
#     characters.
#
#   SCN_REDIS=<host:port> SCN_REDIS_DB=<n> GATE_DJANGO_MANAGE=<manage.py wrapper> \
#     bash gates/W3b_isolation_live.sh
set -u
GATES_DIR=$(cd "$(dirname "$0")" && pwd)
source "$GATES_DIR/lib/django_live.sh"
source "$GATES_DIR/../lib_mcp.sh"
w3_need_django

W3B_FIX=$GATES_DIR/../fixtures/M9
A_TOKEN=scn-w3b-org-a-upstream-bearer-not-a-secret # fake; A's mock requires it
LIST='{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
sha256() { python3 -I -c 'import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())' "$1"; }
received() { scn_mcp_received "$1" 0 "$2" | jq '.received | length'; }
has() { echo "$1" | tr ',' '\n' | grep -Eq "$2" && echo true || echo false; }
call_body() { python3 -I -c 'import json,sys; print(json.dumps({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":sys.argv[1],"arguments":{}}}))' "$1"; }

scn_mcp_up a --path / --fixtures "$W3B_FIX/a" --auth "bearer:$A_TOKEN"
scn_mcp_up b --path / --fixtures "$W3B_FIX/b"
scn_mcp_up ops --path / --fixtures "$W3B_FIX/ops"
mkdir -p "$SCN_BIN"
TEMPLATE=$SCN_BIN/w3b-isolation.yaml
sed -e "s/@@W3B_A_PORT@@/$SCN_MCP_A_PORT/" -e "s/@@W3B_B_PORT@@/$SCN_MCP_B_PORT/" \
  -e "s/@@W3B_OPS_PORT@@/$SCN_MCP_OPS_PORT/" "$GATES_DIR/fixtures/W3b/isolation.yaml" >"$TEMPLATE"
grep -q '@@W3B_' "$TEMPLATE" && scn_fail setup "unrendered placeholders in $TEMPLATE"
scn_mcp_gateway_template "$TEMPLATE"
SCN_CATALOG_WAIT_SKIP=all scn_up
bs_django_up

bs_org "W3b isolation A $(date -u +%Y-%m-%dT%H:%M:%SZ)"
ORG_A=$BS_ORG
bs_org "W3b isolation B $(date -u +%Y-%m-%dT%H:%M:%SZ)"
ORG_B=$BS_ORG
KEY_A=$(scn_key "$ORG_A")
KEY_B=$(scn_key "$ORG_B")
[ -n "$KEY_A" ] && [ -n "$KEY_B" ] || scn_fail setup "no keys for the two orgs"

scn_check "1. A's config and vault credential from Django: the push gives A's pool the secret, redacted on read"
bs_django_py vault "
from accounts.models.organization import Organization
from agentcc.services import mcp_credentials as vault
from agentcc.services.org_config_writer import write_config_version
org = Organization.objects.get(pk=BS_ARGS[0])
write_config_version(org, {'mcp': {'enabled': True, 'access': {'default': 'allow'},
    'servers': {'github': {'url': BS_ARGS[1], 'transport': 'http', 'credential': {'mode': 'shared'}}}}},
    description='W3b gate: org A')
change = vault.create(org, 'github', kind='bearer', value=BS_ARGS[2], actor=None)
bs_out({'credential_id': str(change.credential.pk)})" "$ORG_A" "$SCN_MCP_A_URL/mcp" "$A_TOKEN"
CRED_A=$(jq -r .credential_id "$SCN_DIR/bs-vault.json")
scn_org_config "$ORG_B" "{\"mcp\":{\"enabled\":true,\"access\":{\"default\":\"allow\"},\"servers\":{\"github\":{\"url\":\"$SCN_MCP_B_URL/mcp\"}}}}"
deadline=$(( $(date +%s) + 20 )) # the pool connects in the background
while :; do
  scn_admin GET "/-/mcp/status?org_id=$ORG_A" >/dev/null
  [ "$(jq -r '[.servers[] | select(.server_id == "github") | .healthy][0]' "$SCN_DIR/last.body")" = true ] && break
  [ "$(date +%s)" -ge "$deadline" ] && scn_fail 1.a_healthy "A's github never became healthy: $(cat "$SCN_DIR/last.body")"
  sleep 0.5
done
scn_assert_jq 1.a_github_healthy '[.servers[] | select(.server_id == "github") | .healthy][0]' true
scn_assert_eq 1.a_mock_saw_the_vault_secret "$(sha256 "Bearer $A_TOKEN")" \
  "$(scn_mcp_received a 1 | jq -r '.received[0].auth.sha256 // ""')"
scn_assert_eq 1.config_read 200 "$(scn_admin GET "/-/orgs/$ORG_A/config")"
scn_assert_eq 1.no_secret_in_config no "$(grep -qF "$A_TOKEN" "$SCN_DIR/last.body" && echo yes || echo no)"
scn_assert_jq 1.secret_masked '.mcp.servers.github.credential.secret.value' '****'
scn_assert_jq 1.vault_reference_kept '.mcp.servers.github.credential.credential_id' "$CRED_A"
scn_assert_eq 1.configs_list 200 "$(scn_admin GET /-/orgs/configs)"
scn_assert_eq 1.no_secret_in_list no "$(grep -qF "$A_TOKEN" "$SCN_DIR/last.body" && echo yes || echo no)"

scn_check "2. each key lists and calls only its own org's github"
a_calls=$(received a tools/call)
b_calls=$(received b tools/call)
SCN_KEY=$KEY_A scn_mcp_session_init
SESSION_A=$SCN_MCP_SESSION
scn_assert_eq 2.a_list 200 "$(SCN_KEY=$KEY_A scn_mcp_post /mcp "$LIST")"
a_listed=$(jq -r '[.result.tools[].name] | join(",")' <"$SCN_DIR/last.body")
scn_assert_eq 2.a_lists_own true "$(has "$a_listed" '^github__a_search_issues$')"
scn_assert_eq 2.a_lists_no_other false "$(has "$a_listed" 'b_delete_repo|ops_restart')"
body=$(call_body github__a_search_issues)
scn_assert_eq 2.a_call 200 "$(SCN_KEY=$KEY_A scn_mcp_post /mcp "$body")"
scn_assert_jq 2.a_call_result '.result.isError // false' false
scn_assert_eq 2.a_call_on_a "$((a_calls + 1))" "$(received a tools/call)"
scn_assert_eq 2.a_call_not_on_b "$b_calls" "$(received b tools/call)"
unset SCN_MCP_SESSION
SCN_KEY=$KEY_B scn_mcp_session_init
SESSION_B=$SCN_MCP_SESSION
scn_assert_eq 2.b_list 200 "$(SCN_KEY=$KEY_B scn_mcp_post /mcp "$LIST")"
b_listed=$(jq -r '[.result.tools[].name] | join(",")' <"$SCN_DIR/last.body")
scn_assert_eq 2.b_lists_own true "$(has "$b_listed" '^github__b_delete_repo$')"
scn_assert_eq 2.b_lists_no_other false "$(has "$b_listed" 'a_search_issues|a_open_issue|ops_restart')"
body=$(call_body github__b_delete_repo)
scn_assert_eq 2.b_call 200 "$(SCN_KEY=$KEY_B scn_mcp_post /mcp "$body")"
scn_assert_eq 2.b_call_on_b "$((b_calls + 1))" "$(received b tools/call)"
scn_assert_eq 2.b_call_not_on_a "$((a_calls + 1))" "$(received a tools/call)"
body=$(call_body github__a_search_issues)
scn_assert_eq 2.b_cannot_call_a 200 "$(SCN_KEY=$KEY_B scn_mcp_post /mcp "$body")"
scn_assert_jq 2.b_cannot_call_a_code '.error.code' -32602
scn_assert_eq 2.still_not_on_a "$((a_calls + 1))" "$(received a tools/call)"

scn_check "3. sessions, anonymous methods and both eras on one endpoint"
SCN_MCP_SESSION=$SESSION_A
scn_assert_eq 3.a_session_404_for_b 404 "$(SCN_KEY=$KEY_B scn_mcp_post /mcp "$LIST")"
SCN_MCP_SESSION=$SESSION_A
scn_assert_eq 3.a_session_still_a 200 "$(SCN_KEY=$KEY_A scn_mcp_post /mcp "$LIST")"
unset SCN_MCP_SESSION
anon() { # <method> [body]: /mcp with no credential; prints the status
  local args=(-sS -o "$SCN_DIR/last.body" -D "$SCN_DIR/last.headers" -w '%{http_code}' -X "$1" "$SCN_GW/mcp"
    -H 'Accept: application/json, text/event-stream')
  [ $# -ge 2 ] && args+=(-H 'Content-Type: application/json' --data-binary "$2")
  curl "${args[@]}"
}
scn_assert_eq 3.anonymous_post_401 401 "$(anon POST "$LIST")"
scn_assert_eq 3.anonymous_get_401 401 "$(anon GET)"
scn_assert_eq 3.anonymous_delete_401 401 "$(anon DELETE)"
body26=$(python3 -I -c 'import json; print(json.dumps({"jsonrpc":"2.0","id":9,"method":"tools/list","params":{"_meta":{
  "io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{},
  "io.modelcontextprotocol/clientInfo":{"name":"w3b-gate","version":"1"}}}}))')
scn_assert_eq 3.stateless_2026 200 "$(SCN_KEY=$KEY_A SCN_MCP_SESSION="" scn_mcp_post /mcp "$body26" \
  "MCP-Protocol-Version: 2026-07-28" "Mcp-Method: tools/list")"
scn_assert_jq 3.stateless_2026_lists_a '[.result.tools[].name] | index("github__a_search_issues") != null' true
scn_assert_jq 3.stateless_2026_result_type '.result.resultType' complete
SCN_MCP_SESSION=$SESSION_B
scn_assert_eq 3.legacy_session_same_endpoint 200 "$(SCN_KEY=$KEY_B scn_mcp_post /mcp "$LIST")"
scn_assert_jq 3.legacy_no_result_type '.result.resultType // "absent"' absent
unset SCN_MCP_SESSION

scn_check "4. the admin playground meters one admin record; B probes a 300-character hidden tool"
before_admin=$(received a tools/call)
test_body="{\"org_id\":\"$ORG_A\",\"name\":\"github__a_search_issues\",\"arguments\":{}}"
scn_assert_eq 4.admin_test 200 "$(scn_admin POST /-/mcp/test "$test_body")"
scn_assert_jq 4.admin_test_ok '(.error // "") + "|" + (.is_error | tostring)' "|false"
scn_assert_eq 4.admin_test_on_a "$((before_admin + 1))" "$(received a tools/call)"
PROBE=$(python3 -I -c 'print("p" * 300)')
SCN_KEY=$KEY_B scn_mcp_session_init
body=$(call_body "$PROBE")
scn_assert_eq 4.probe 200 "$(SCN_KEY=$KEY_B scn_mcp_post /mcp "$body")"
scn_assert_jq 4.probe_unknown '.error.code' -32602
unset SCN_MCP_SESSION

scn_check "5. one webhook flush of both orgs' tool_call events ingests under the right org"
RECORDS=$SCN_DIR/w3b-records.json
deadline=$(( $(date +%s) + 30 ))
while :; do
  curl -sS "$SCN_MOCK/__logs" >"$SCN_DIR/w3b-logs.json"
  jq --arg a "$ORG_A" --arg b "$ORG_B" '[.logs[] | select(.kind == "tool_call" and (.org_id == $a or .org_id == $b))]' \
    "$SCN_DIR/w3b-logs.json" >"$RECORDS"
  [ "$(jq length "$RECORDS")" -ge 5 ] && break
  [ "$(date +%s)" -ge "$deadline" ] && scn_fail 5.records "only $(jq length "$RECORDS") tool_call records after 30 s"
  sleep 0.5
done
# A's call, A's admin test, B's call, B's call on A's tool (unknown), B's probe.
scn_assert_jq 5.five_records 'length' 5 "$RECORDS"
scn_assert_jq 5.a_records "[.[] | select(.org_id == \"$ORG_A\")] | length" 2 "$RECORDS"
scn_assert_jq 5.b_records "[.[] | select(.org_id == \"$ORG_B\")] | length" 3 "$RECORDS"
scn_assert_jq 5.admin_record "[.[] | select(.principal_kind == \"admin\")] | [length, .[0].org_id] | map(tostring) | join(\",\")" "1,$ORG_A" "$RECORDS"
scn_assert_jq 5.probe_cut '[.[] | select(.tool | startswith("ppp"))] | [length, .[0].tool | length] | map(tostring) | join(",")' "1,255" "$RECORDS"
jq -c '{logs: .logs}' "$SCN_DIR/w3b-logs.json" >"$SCN_DIR/w3b-batch.json"
bs_django_py ingest "
import json
from agentcc.models.mcp_tool_call import AgentccToolCall
from agentcc.services.log_ingestion import ingest_request_logs
batch = json.load(open(BS_ARGS[0]))['logs']
records = json.load(open(BS_ARGS[1]))
ingest_request_logs(batch)
ingest_request_logs(batch)  # a replayed flush stores nothing twice (event_id)
rows = {r.event_id: r for r in AgentccToolCall.objects.filter(event_id__in=[x['event_id'] for x in records])}
bs_out({
  'stored': len(rows),
  'right_org': sum(1 for x in records if x['event_id'] in rows and str(rows[x['event_id']].organization_id) == x['org_id']),
  'admin': [r.principal_kind for r in rows.values() if r.principal_kind == 'admin'],
  'probe_tool_len': [len(r.tool) for r in rows.values() if r.tool.startswith('ppp')],
  'a_rows': sum(1 for r in rows.values() if str(r.organization_id) == BS_ARGS[2]),
  'b_rows': sum(1 for r in rows.values() if str(r.organization_id) == BS_ARGS[3]),
})" "$SCN_DIR/w3b-batch.json" "$RECORDS" "$ORG_A" "$ORG_B"
I=$SCN_DIR/bs-ingest.json
echo "  ingested: $(jq -c . "$I")"
scn_assert_jq 5.stored '.stored' 5 "$I"
scn_assert_jq 5.under_the_right_org '.right_org' 5 "$I"
scn_assert_jq 5.org_a_rows '.a_rows' 2 "$I"
scn_assert_jq 5.org_b_rows '.b_rows' 3 "$I"
scn_assert_jq 5.admin_row '.admin | length' 1 "$I"
scn_assert_jq 5.probe_row_255 '.probe_tool_len | join(",")' 255 "$I"
scn_pass
