#!/usr/bin/env bash
# Native server-tool metering and route support on the real binary. The mock
# upstream (agentcc-mockup) never reports Anthropic's
# usage.server_tool_use, so the Anthropic provider (a direct source, Anthropic's
# own API) points at a stdlib stand-in (fixtures/server-tools/anthropic_standin.py) that
# answers a recorded Messages body with server_tool_use.web_search_requests 3
# when the request carries a web search tool, and the same tokens without
# searches otherwise; it records what it received. The org calls it with its
# own key (BYOK). Bedrock serves the same Claude model, at the same stand-in,
# so a call that reached it is recorded. The request log goes to the mock
# upstream's /__logs (fixtures/server-tools/gateway.yaml).
#  1. A BYOK call carrying web_search_20250305 reaches the upstream with the
#     tool as written, and the client sees the three searches in its usage.
#  2. Its attempt's upstream estimate is the tokens plus 30,000 fee micros
#     ($10/1k searches): the same call without searches, the same tokens,
#     estimates 30,000 less. The searching attempt is usage_estimated (the
#     fee is the gateway's estimate of the customer's own bill); the plain
#     one is not. Nothing is billed: BYOK.
#  3. The same request with model pinned to a Bedrock Claude is 403
#     model_not_permitted with metadata.reason server_tool_unsupported, and
#     no upstream is called.
#  4. A Bedrock request with only a function tool is not refused by the rule:
#     it reaches Bedrock's route.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> bash scripts/scenarios/04_server_tools.sh
#
# set -u only, like the other scenarios: lib.sh's teardown kills processes
# that may already be gone. Every step that can fail checks for it.
set -u
source "$(dirname "$0")/lib.sh"

FIX=$SCN_ROOT/fixtures/server-tools
CLAUDE=claude-sonnet-4-5-20250929
BEDROCK_CLAUDE=anthropic.claude-sonnet-4-5-20250929-v1:0
SEARCH_TOOL='{"type":"web_search_20250305","name":"web_search","max_uses":3,"allowed_domains":["uefa.com"]}'

# chat <model> [tools json]: a one-message chat body.
chat() {
  python3 -I -c 'import json, sys
b = {"model": sys.argv[1], "messages": [{"role": "user", "content": "Who won Euro 2024?"}], "max_tokens": 256}
if len(sys.argv) > 2 and sys.argv[2]:
    b["tools"] = json.loads(sys.argv[2])
print(json.dumps(b))' "$@"
}
received() { wc -l <"$WORK/received.jsonl" 2>/dev/null | tr -d ' ' || echo 0; }

# log_entry <request id> <file>: wait for the request's log entry at the mock.
log_entry() {
  local deadline=$(( $(date +%s) + 30 ))
  while :; do
    curl -sS "$SCN_MOCK/__logs" | jq --arg id "$1" '[.logs[] | select(.request_id == $id)] | first // empty' >"$2"
    [ -s "$2" ] && return 0
    [ "$(date +%s)" -ge "$deadline" ] && return 1
    sleep 0.5
  done
}

# The stand-in and the rendered config live in the run's scratch dir (scn_up
# empties SCN_DIR). The stand-in exits by itself when this shell does.
WORK=$SCN_HOME/scn-server-tools
rm -rf "$WORK"
mkdir -p "$WORK" || scn_fail setup "cannot create $WORK"
: >"$WORK/received.jsonl"
UP_PORT=$(_scn_free_port)
python3 -I "$FIX/anthropic_standin.py" "$UP_PORT" "$FIX" "$WORK" $$ >"$WORK/standin.log" 2>&1 &
UP_PID=$!
deadline=$(( $(date +%s) + 10 ))
until curl -sf -o /dev/null "http://127.0.0.1:$UP_PORT/__health"; do
  kill -0 "$UP_PID" 2>/dev/null || scn_fail setup "the Anthropic stand-in exited: $(tail -5 "$WORK/standin.log")"
  [ "$(date +%s)" -ge "$deadline" ] && scn_fail setup "the Anthropic stand-in is not answering on port $UP_PORT"
  sleep 0.2
done
sed "s#@@ANTHROPIC_URL@@#http://127.0.0.1:$UP_PORT#" "$FIX/gateway.yaml" >"$WORK/gateway.yaml" || scn_fail setup "cannot write the config"

export SCN_CONFIG_TEMPLATE=$WORK/gateway.yaml
export SCN_CATALOG_WAIT_SKIP=all # the BYOK calls need no managed catalog
scn_up
trap 'kill "$UP_PID" 2>/dev/null; scn_down' EXIT

# The org's own credentials for both providers (BYOK; fake values): a key for
# Anthropic, and AWS keys for Bedrock.
scn_org_config "$SCN_ORG" "$(python3 -I -c 'import json, sys
print(json.dumps({"providers": {
    "anthropic": {"api_key": "scn-test-byok-anthropic", "models": [sys.argv[1]], "enabled": True},
    "bedrock": {"aws_access_key_id": "scn-test-byok-aws-key", "aws_secret_access_key": "scn-test-byok-aws-secret",
                "aws_region": "us-east-1", "models": [sys.argv[2]], "enabled": True},
}}))' "$CLAUDE" "$BEDROCK_CLAUDE")"

scn_check "1. a BYOK call with web search reaches Anthropic with the tool as written"
code=$(scn_chat "$(chat "$CLAUDE" "[$SEARCH_TOOL]")")
scn_assert_eq 1.status 200 "$code"
SEARCH_ID=$(scn_hdr x-agentcc-request-id)
[ -n "$SEARCH_ID" ] || scn_fail 1.request_id "no x-agentcc-request-id header"
scn_assert_jq 1.searches_in_usage '.usage.server_tool_use.web_search_requests' "3"
scn_assert_jq 1.answer '.choices[0].message.content' "Spain won Euro 2024, beating England 2-1 in the final."
scn_assert_eq 1.upstream_calls 1 "$(received)"
tail -1 "$WORK/received.jsonl" >"$SCN_DIR/upstream.json"
scn_assert_jq 1.path '.path' "/v1/messages" "$SCN_DIR/upstream.json"
scn_assert_jq 1.org_key '.api_key' "scn-test-byok-anthropic" "$SCN_DIR/upstream.json"
scn_assert_eq 1.tool_verbatim "$(echo "$SEARCH_TOOL" | jq -cS .)" "$(jq -r .body <"$SCN_DIR/upstream.json" | jq -cS '.tools[0]')"

scn_check "2. the attempt estimates the tokens plus 30,000 fee micros, and bills nothing"
code=$(scn_chat "$(chat "$CLAUDE")")
scn_assert_eq 2.plain_status 200 "$code"
PLAIN_ID=$(scn_hdr x-agentcc-request-id)
scn_assert_jq 2.plain_no_searches '.usage.server_tool_use == null' "true"
scn_assert_eq 2.plain_upstream_calls 2 "$(received)"
log_entry "$SEARCH_ID" "$SCN_DIR/search-entry.json" || scn_fail 2.search_entry "no request log entry for $SEARCH_ID"
log_entry "$PLAIN_ID" "$SCN_DIR/plain-entry.json" || scn_fail 2.plain_entry "no request log entry for $PLAIN_ID"
for f in search plain; do
  scn_assert_jq "2.$f.billing_mode" '.metadata.billing_mode' "byok" "$SCN_DIR/$f-entry.json"
  scn_assert_jq "2.$f.credential_type" '.attempts[0].credential_type' "byok" "$SCN_DIR/$f-entry.json"
  scn_assert_jq "2.$f.priced" '.attempts[0].upstream_cost_micros > 0' "true" "$SCN_DIR/$f-entry.json"
  scn_assert_jq "2.$f.nothing_billed" '(.attempts[0].billed_cost_micros // 0) == (.attempts[0].upstream_cost_micros // 0) and (.cost_markup // 0) == 0' "true" "$SCN_DIR/$f-entry.json"
done
SEARCH_UP=$(jq '.attempts[0].upstream_cost_micros' <"$SCN_DIR/search-entry.json")
PLAIN_UP=$(jq '.attempts[0].upstream_cost_micros' <"$SCN_DIR/plain-entry.json")
scn_assert_eq 2.fee_micros 30000 "$(( SEARCH_UP - PLAIN_UP ))"
scn_assert_jq 2.search_attempt_estimated '.attempts[0].usage_estimated' "true" "$SCN_DIR/search-entry.json"
scn_assert_jq 2.search_request_estimated '.usage_estimated' "true" "$SCN_DIR/search-entry.json"
scn_assert_jq 2.plain_not_estimated '(.attempts[0].usage_estimated // false) or (.usage_estimated // false)' "false" "$SCN_DIR/plain-entry.json"

scn_check "3. the same request pinned to a Bedrock Claude is refused before any upstream"
before=$(received)
code=$(scn_chat "$(chat "$BEDROCK_CLAUDE" "[$SEARCH_TOOL]")")
scn_assert_eq 3.status 403 "$code"
scn_assert_jq 3.code '.error.code' "model_not_permitted"
scn_assert_jq 3.reason '.error.metadata.reason' "server_tool_unsupported"
scn_assert_eq 3.upstream_not_called "$before" "$(received)"

scn_check "4. a Bedrock request with only a function tool is not refused by the rule"
FUNCTION_TOOL='{"type":"function","function":{"name":"lookup","parameters":{"type":"object","properties":{}}}}'
before=$(received)
code=$(scn_chat "$(chat "$BEDROCK_CLAUDE" "[$FUNCTION_TOOL]")")
scn_assert_eq 4.not_server_tool_refusal false "$(jq -r '.error.metadata.reason == "server_tool_unsupported"' <"$SCN_DIR/last.body")"
[ "$(received)" -gt "$before" ] || scn_fail 4.reached_bedrock "the request never reached the Bedrock route (HTTP $code: $(cat "$SCN_DIR/last.body"))"
scn_assert_eq 4.bedrock_path true "$(tail -1 "$WORK/received.jsonl" | jq -r '.path | startswith("/model/")')"

scn_pass
