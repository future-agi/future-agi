#!/usr/bin/env bash
# M15: every MCP tools/call is metered. Through the real gateway, with its
# log webhook pointed at the X0 mock and one MCP mock upstream:
#  1. Key A makes six calls: two allowed (primary_echo, primary_get_weather),
#     one isError (the mock armed with is_error), one an ACL denies
#     (primary_delete_file), one to a hidden tool, and one that times out
#     (the mock armed with a latency past mcp.tool_call_timeout). Key B, whose
#     log_content is none, makes one allowed call. Both are org-acme's
#     person-owned keys, listed by the mock's control plane (733). A listed
#     key carries no MCP tool lists (only a deployment identity's config
#     entry does), so the denial is the call's x-agentcc-mcp-allow-tools list,
#     which names only the two allowed tools; it answers in band (tool_denied,
#     rule header), as every denial but the key lists' does on the legacy view.
#  2. /__logs gets exactly seven kind "tool_call" records: each with
#     event_id = tool_call_id, api_key_id, server_id, tool, outcome and
#     billing_mode byok; key A's six outcomes are success, success, is_error,
#     denied (tool_denied), denied (unknown_tool, no server_id) and timeout.
#  3. Every tools/call answer that is a result (the ACL denial's included)
#     carries _meta["io.futureagi/tool_call"] with billing_mode byok,
#     billed_usd 0 and the tool_call_id of that call's record.
#  4. Key B's record keeps no arguments and no result; no record carries a
#     key, a bearer or the session id.
#  6. A probe for a hidden tool with a 300-character name is recorded once,
#     under the name cut to 255 characters (M3's varchar(255) tool column),
#     so one probe cannot fail the webhook batch it shares with other orgs.
#
#   SCN_REDIS=<host:port> SCN_REDIS_DB=<lane db> bash scripts/scenarios/70_m15_meter.sh
#
# Same-wave seams (plan section 5, W3b): the principal, agent context and
# tool_call_id come from M8, the org config from M9, the in-band ACL denial
# from M10 and the in-band timeout from M13. Before the W3b merge the
# gateway resolves none of them, so the attribution values (agent.run_id,
# api_key_id, org_id), key A's content under org content mode full, and the
# _meta block on the ACL denial and the timeout are asserted only at the
# wave gate, with SCN_WAVE_GATE=1. Everything else runs in the lane.
set -u
source "$(dirname "$0")/lib.sh"
source "$SCN_ROOT/lib_mcp.sh"

KEY_A=$SCN_KEY
KEY_B=scn-test-key-acme-quiet
SCN_CP_KEYS='[{"id": "scn-key-acme-quiet", "key": "scn-test-key-acme-quiet", "name": "acme-quiet", "owner": "acme",
  "metadata": {"org_id": "org-acme", "user_id": "scn-user-acme", "log_content": "none"}}]'
ALLOW_TWO="x-agentcc-mcp-allow-tools: primary_echo,primary_get_weather" # key A's ACL for the denied call
RUN_ID=run_M15
GATE=${SCN_WAVE_GATE:-}
mkdir -p "$SCN_HOME/scn-run"
ANSWERS=$SCN_HOME/scn-run/70_m15_answers.jsonl # scn_up wipes $SCN_DIR; this keeps across it

scn_mcp_up primary --era dual
scn_mcp_gateway_template "$SCN_ROOT/fixtures/M15/gateway.yaml"
SCN_CATALOG_WAIT_SKIP=all scn_up # no managed provider in the template
: >"$ANSWERS"

# The org config M9 loads at the gate: content mode full. M10 IR-12, ruled (a)
# at the W3b merge: no mcp section, so org-acme, mcp.static.org_id, keeps the
# legacy view, its YAML server under today's names (an org whose config has
# an mcp section is served through virtual servers, whose compatibility view
# composes only the org's own mcp.servers).
scn_org_config org-acme '{"privacy":{"content_mode":"full"}}'

# call <label> <tool> <arguments json> [header...]: one tools/call on the
# current session, with any extra "Name: value" headers; keeps the label and
# the answer in $ANSWERS.
call() {
  local label=$1 tool=$2 args=$3 code body
  shift 3
  body=$(python3 -I -c 'import json,sys; print(json.dumps({"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":sys.argv[1],"arguments":json.loads(sys.argv[2])}}))' "$tool" "$args")
  code=$(scn_mcp_post /mcp "$body" "x-agentcc-run-id: $RUN_ID" "x-agentcc-agent-id: m15-agent" "$@")
  [ "$code" = 200 ] || scn_fail "1.$label.status" "tools/call $tool returned HTTP $code: $(cat "$SCN_DIR/last.body")"
  python3 -I -c 'import json,sys; print(json.dumps({"label":sys.argv[1],"tool":sys.argv[2],"answer":json.load(open(sys.argv[3]))}))' \
    "$label" "$tool" "$SCN_DIR/last.body" >>"$ANSWERS"
}

scn_check "1. key A makes six calls, key B one"
SCN_KEY=$KEY_A scn_mcp_session_init 2025-06-18
SESSION_A=$SCN_MCP_SESSION
SCN_KEY=$KEY_A call allowed_echo primary_echo '{"text":"hello"}'
SCN_KEY=$KEY_A call allowed_weather primary_get_weather '{"city":"Paris"}'
scn_mcp_control primary '{"is_error":true}' >/dev/null || scn_fail 1.arm_is_error "scn_mcp_control failed"
SCN_KEY=$KEY_A call is_error primary_echo '{"text":"fails"}'
scn_mcp_control primary '{}' >/dev/null || scn_fail 1.disarm "scn_mcp_control failed"
SCN_KEY=$KEY_A call acl_denied primary_delete_file '{"path":"/etc/hosts"}' "$ALLOW_TWO"
SCN_KEY=$KEY_A call hidden primary_no_such_tool '{"probe":true}'
scn_mcp_control primary '{"latency_ms":2500}' >/dev/null || scn_fail 1.arm_latency "scn_mcp_control failed"
SCN_KEY=$KEY_A call timeout primary_echo '{"text":"slow"}'
scn_mcp_control primary '{}' >/dev/null || scn_fail 1.disarm "scn_mcp_control failed"
SCN_KEY=$KEY_B scn_mcp_session_init 2025-06-18
SCN_KEY=$KEY_B call quiet primary_echo '{"text":"quiet secret words"}'
scn_assert_eq 1.answers 7 "$(wc -l <"$ANSWERS" | tr -d ' ')"
scn_assert_eq 1.is_error_answered true "$(jq -rs '.[] | select(.label=="is_error") | .answer.result.isError' "$ANSWERS")"
scn_assert_eq 1.hidden_refused yes "$(jq -rs '.[] | select(.label=="hidden") | if .answer.error then "yes" else "no" end' "$ANSWERS")"

scn_check "2. the log webhook carries seven tool_call records"
RECORDS=$SCN_DIR/records.json
deadline=$(( $(date +%s) + 30 ))
while :; do
  curl -sS "$SCN_MOCK/__logs" >"$SCN_DIR/logs.json"
  jq '[.logs[] | select(.kind == "tool_call")]' "$SCN_DIR/logs.json" >"$RECORDS"
  [ "$(jq length "$RECORDS")" -ge 7 ] && break
  [ "$(date +%s)" -ge "$deadline" ] && scn_fail 2.records "only $(jq length "$RECORDS") tool_call records after 30 s"
  sleep 0.5
done
scn_assert_jq 2.count 'length' 7 "$RECORDS"
scn_assert_jq 2.v_and_ids '[.[] | select(.v == 1 and (.event_id | length) > 0 and .event_id == .tool_call_id)] | length' 7 "$RECORDS"
scn_assert_jq 2.ids_distinct '[.[].event_id] | unique | length' 7 "$RECORDS"
scn_assert_jq 2.fields_present \
  '[.[] | select(has("api_key_id") and has("server_id") and (.tool | length) > 0 and (.outcome | length) > 0 and has("agent"))] | length' 7 "$RECORDS"
scn_assert_jq 2.billing_mode_byok '[.[] | select(.billing_mode == "byok" and .billed_cost_micros == 0)] | length' 7 "$RECORDS"
# Key A's six records are the first six (calls are sequential); key B's is last.
scn_assert_jq 2.outcomes '[.[:6][] | .outcome] | join(",")' 'success,success,is_error,denied,denied,timeout' "$RECORDS"
scn_assert_jq 2.tools '[.[:6][] | .tool] | join(",")' \
  'primary_echo,primary_get_weather,primary_echo,primary_delete_file,primary_no_such_tool,primary_echo' "$RECORDS"
scn_assert_jq 2.acl_denial '.[3] | [.error_code, .error_class, .error_origin] | join(",")' 'tool_denied,denied,gateway' "$RECORDS"
scn_assert_jq 2.hidden_tool '.[4] | [.error_code, .error_class, .server_id] | join(",")' 'unknown_tool,denied,' "$RECORDS"
scn_assert_jq 2.timeout '.[5] | [.error_class, .error_origin, (.dispatched | tostring)] | join(",")' 'timeout,upstream,true' "$RECORDS"
scn_assert_jq 2.timeout_latency '.[5].latency_ms >= 1000' true "$RECORDS"
scn_assert_jq 2.is_error '.[2] | [(.is_error | tostring), .error_origin] | join(",")' 'true,upstream' "$RECORDS"
scn_assert_jq 2.allowed_server '[.[0,1,2,5] | .server_id] | unique | join(",")' primary "$RECORDS"
scn_assert_jq 2.upstream_tools '[.[0], .[1]] | map(.upstream_tool) | join(",")' 'echo,get_weather' "$RECORDS"

scn_check "3. every result carries the evidence block of its own record"
python3 -I - "$ANSWERS" "$RECORDS" "$GATE" >"$SCN_DIR/evidence.txt" 2>&1 <<'PY' || scn_fail 3.evidence "$(cat "$SCN_DIR/evidence.txt")"
import json, sys
answers = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8")]
records = json.load(open(sys.argv[2], encoding="utf-8"))
gate = sys.argv[3] == "1"
blocks = 0
for a, rec in zip(answers, records):
    res = a["answer"].get("result")
    if res is None:
        # A JSON-RPC error carries no result: the hidden tool only. The ACL
        # denial (the request's allow list, rule header) answers in band, and
        # since the W3b merge so does the timeout (M13), with or without
        # SCN_WAVE_GATE.
        if a["label"] == "hidden":
            continue
        sys.exit("%s: answered %s, want a result" % (a["label"], a["answer"].get("error")))
    meta = (res.get("_meta") or {}).get("io.futureagi/tool_call")
    if not meta:
        sys.exit("%s: result has no io.futureagi/tool_call block: %s" % (a["label"], json.dumps(res)))
    want = {"tool_call_id": rec["tool_call_id"], "billing_mode": "byok", "billed_usd": 0}
    got = {k: meta.get(k) for k in want}
    if got != want or set(meta) != {"tool_call_id", "executed", "billing_mode", "billed_usd", "latency_ms"}:
        sys.exit("%s: block %s, want %s (record %s)" % (a["label"], meta, want, rec["tool"]))
    if a["tool"] != rec["tool"]:
        sys.exit("%s: answer for %s matched the record of %s" % (a["label"], a["tool"], rec["tool"]))
    blocks += 1
print(blocks)
PY
# Six results carry the block: the two allowed calls, is_error, the ACL
# denial (in band: the request's allow list), the timeout (in band since M13)
# and key B's call; the hidden tool is -32602. Key lists, whose legacy-view
# denial stays -32600 (M10 IR-12 (a)), exist only on deployment identities
# under a control plane (733).
scn_assert_eq 3.blocks 6 "$(cat "$SCN_DIR/evidence.txt")"

scn_check "4. key B's record keeps no content; no record carries a credential or the session id"
scn_assert_jq 4.quiet_tool '.[6].tool' primary_echo "$RECORDS"
scn_assert_jq 4.quiet_content_mode '.[6].content_mode' none "$RECORDS"
scn_assert_jq 4.quiet_no_arguments '.[6] | has("arguments") or has("result")' false "$RECORDS"
scn_assert_eq 4.quiet_words_absent 0 "$(grep -c 'quiet secret words' "$RECORDS")"
scn_assert_eq 4.no_keys 0 "$(grep -c -e "$KEY_A" -e "$KEY_B" -e 'Bearer' "$SCN_DIR/logs.json")"
scn_assert_eq 4.no_session_id 0 "$(grep -c -F -e "$SESSION_A" -e "$SCN_MCP_SESSION" "$SCN_DIR/logs.json")"
scn_assert_jq 4.session_hashed '[.[] | select((.session_hash // "") | test("^[0-9a-f]{64}$"))] | length' 7 "$RECORDS"

if [ -n "$GATE" ]; then
  scn_check "5. (wave gate) attribution and content from M8, M9, M10 and M13"
  scn_assert_jq 5.run_id '[.[] | select(.agent.run_id == "run_M15")] | length' 7 "$RECORDS"
  scn_assert_jq 5.api_key_id '[.[] | select((.api_key_id | length) > 0 and .org_id == "org-acme")] | length' 7 "$RECORDS"
  scn_assert_jq 5.keys_differ '[.[:6][].api_key_id] | unique | length' 1 "$RECORDS"
  scn_assert_jq 5.quiet_key '.[6].api_key_id != .[0].api_key_id' true "$RECORDS"
  scn_assert_jq 5.full_content '.[0] | [.content_mode, (.arguments.text // "")] | join(",")' 'full,hello' "$RECORDS"
else
  echo "  (attribution, org content mode full and the in-band ACL and timeout blocks: SCN_WAVE_GATE=1, after M8-M13 merge)"
fi

scn_check "6. a hidden-tool probe with a 300-character name is recorded under 255 characters"
LONG_TOOL=primary_probe_$(python3 -I -c 'print("\u00e9" * 286)') # 300 characters, two bytes each after the prefix
SCN_KEY=$KEY_B call long_probe "$LONG_TOOL" '{}'
deadline=$(( $(date +%s) + 30 ))
while :; do
  curl -sS "$SCN_MOCK/__logs" >"$SCN_DIR/logs.json"
  jq '[.logs[] | select(.kind == "tool_call")]' "$SCN_DIR/logs.json" >"$RECORDS"
  [ "$(jq length "$RECORDS")" -ge 8 ] && break
  [ "$(date +%s)" -ge "$deadline" ] && scn_fail 6.records "only $(jq length "$RECORDS") tool_call records after 30 s"
  sleep 0.5
done
scn_assert_jq 6.count 'length' 8 "$RECORDS"
scn_assert_jq 6.tool_cut '.[7] | [(.tool | length | tostring), (.tool | startswith("primary_probe_\u00e9") | tostring)] | join(",")' '255,true' "$RECORDS"
scn_assert_jq 6.probe_denied '.[7] | [.outcome, .error_code, .server_id] | join(",")' 'denied,unknown_tool,' "$RECORDS"
scn_pass
