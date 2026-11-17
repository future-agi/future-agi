#!/usr/bin/env bash
# Cloud-only, loopback-only proof of the approvals kernel; not a combined acceptance run.
# Like the shared scenario library, fail via scn_assert/scn_fail rather than
# errexit: best-effort cleanup may signal a process that has already exited.
set -u
case "${SCN_REDIS:-}" in 127.0.0.1:*) ;; *) echo 'the approvals proof requires loopback SCN_REDIS' >&2; exit 2;; esac
source "$(dirname "$0")/lib.sh"
source "$(dirname "$0")/lib_mcp.sh"
scn_mcp_up approval --fixtures "$SCN_ROOT/fixtures/m26"
sed "s/@@APPROVAL_PORT@@/$SCN_MCP_APPROVAL_PORT/" "$SCN_ROOT/fixtures/m26/gateway.yaml" > "$SCN_BIN/m26-gateway.yaml"
scn_mcp_gateway_template "$SCN_BIN/m26-gateway.yaml"
SCN_CATALOG_WAIT_SKIP=all scn_up
scn_org_config "$SCN_ORG" "$(jq -nc --arg url "$SCN_MCP_APPROVAL_URL/mcp" '{mcp:{enabled:true,access:{default:"allow"},servers:{mock:{url:$url}},virtual_servers:{review:{id:"vs_01M4ZEB9E0J3AADR85Z4T4428X",members:["mock"],access:{default_for_org:true},approval:{require:"destructive",validity:"once",ttl_seconds:120}}}}}')"
RESOURCE=/mcp/v/vs_01M4ZEB9E0J3AADR85Z4T4428X
scn_mcp_session_init 2025-06-18 "$RESOURCE"
# Wait only for the synthetic pool's asynchronously fetched tool definition.
ready=0
for _ in $(seq 1 100); do
 code=$(scn_mcp_post "$RESOURCE" '{"jsonrpc":"2.0","id":2,"method":"tools/list"}')
 if [ "$code" = 200 ] && jq -e '.result.tools | any(.name == "mock__delete_repo")' "$SCN_DIR/last.body" >/dev/null; then ready=1; break; fi
 sleep 0.1
done
scn_assert_eq pool.ready 1 "$ready"
CALL='{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"mock__delete_repo","arguments":{"repo":"synthetic-only"}}}'
code=$(scn_mcp_post "$RESOURCE" "$CALL")
scn_assert_eq pending.http 200 "$code"
scn_assert_jq pending.error '.result.isError' true
scn_assert_jq pending.code '.result._meta["io.futureagi/error"].code' approval_required
scn_assert_jq pending.status '.result._meta["io.futureagi/error"].metadata.status' pending
ID=$(jq -r '.result._meta["io.futureagi/error"].metadata.approval_id' "$SCN_DIR/last.body")
POLL=$(jq -r '.result._meta["io.futureagi/error"].metadata.poll_url' "$SCN_DIR/last.body")
scn_assert_eq pending.poll "$RESOURCE/approvals/$ID" "$POLL"
scn_assert_eq pending.not_executed 0 "$(scn_mcp_received approval 0 tools/call | jq '.received | length')"
code=$(scn_admin POST "/-/mcp/approvals/$ID/decision" "$(jq -nc --arg org "$SCN_ORG" '{org_id:$org,decision:"approve",actor:"synthetic-admin"}')")
scn_assert_eq decision.http 200 "$code"
scn_assert_jq decision.status '.status' approved
code=$(curl -sS -o "$SCN_DIR/poll.json" -w '%{http_code}' -H "Authorization: Bearer $SCN_KEY" "$SCN_GW$POLL")
scn_assert_eq poll.http 200 "$code"
scn_assert_jq poll.status '.status' approved "$SCN_DIR/poll.json"
RETRY=$(jq -c --arg id "$ID" '.params._meta={"io.futureagi/approval_id":$id}' <<< "$CALL")
code=$(scn_mcp_post "$RESOURCE" "$RETRY")
scn_assert_eq retry.http 200 "$code"
scn_assert_jq retry.ok '.result.isError // false' false
scn_assert_eq retry.executed_once 1 "$(scn_mcp_received approval 0 tools/call | jq '.received | length')"
code=$(scn_mcp_post "$RESOURCE" "$RETRY")
scn_assert_eq replay.http 200 "$code"
scn_assert_jq replay.refused '.result.isError' true
scn_assert_eq replay.not_executed 1 "$(scn_mcp_received approval 0 tools/call | jq '.received | length')"
scn_pass
