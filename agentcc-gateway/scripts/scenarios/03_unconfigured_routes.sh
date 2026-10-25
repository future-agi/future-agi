#!/usr/bin/env bash
# Unconfigured routes answer 501 and org FutureAGI checks fail closed, on the
# real binary against the mock upstream (fixtures/guardrails/gateway.yaml:
# base.yaml with guardrails on).
#  1. With no video provider configured, every /v1/videos route answers 501
#     endpoint_not_configured.
#  2. Every /v1/scheduled route answers 501 endpoint_not_configured.
#  3. Control: with no guardrail in its org config, the org's chat passes.
#  4. An org-config FutureAGI guardrail (action block) that lacks eval_id
#     blocks the chat (403 content_blocked, naming the rule and eval_id) and
#     sends nothing to the FutureAGI evaluation API (here the mock).
#  5. Control for 4: the same rule with an eval id does call the evaluation
#     API at the mock, which has no such route, and the failed call still
#     blocks the chat. So 4's "nothing sent" is a real zero. The rule reaches
#     the mock as http://localhost:<port>, the name the operator listed in
#     mcp.egress.private_allow (fixtures/guardrails/gateway.yaml).
#  6. The same rule naming the mock as http://127.0.0.1:<port>, an address the
#     operator did not list, is refused by the gateway's egress guard: the chat
#     is blocked and the mock gets no call (an org FutureAGI eval check reaches
#     a loopback or private address only when the operator lists it).
#
# The startup refusals (routing.scheduled.enabled: true, or a YAML FutureAGI
# rule with no eval_id) are config validation's; catalog_credits_live_rows.sh
# checks them.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> bash scripts/scenarios/03_unconfigured_routes.sh
set -u
SCN_CONFIG_TEMPLATE="$(cd "$(dirname "$0")" && pwd)/fixtures/guardrails/gateway.yaml"
source "$(dirname "$0")/lib.sh"

CHAT='{"model":"anthropic/claude-sonnet-5.5","messages":[{"role":"user","content":"v1 guarded prompt"}]}'
EVAL_PATH=/sdk/api/v1/eval

# v1_call <method> <path> [json]: a request with $SCN_KEY; prints the status
# and keeps headers and body in $SCN_DIR/last.*.
v1_call() {
  local args=(-sS -o "$SCN_DIR/last.body" -D "$SCN_DIR/last.headers" -w '%{http_code}' -X "$1" "$SCN_GW$2"
    -H "Authorization: Bearer $SCN_KEY")
  [ $# -ge 3 ] && args+=(-H 'Content-Type: application/json' --data-binary "$3")
  curl "${args[@]}"
}

# assert_not_configured <label> <method> <path> [json]: the route answers 501
# endpoint_not_configured.
assert_not_configured() {
  local label=$1 code
  shift
  code=$(v1_call "$@")
  scn_assert_eq "$label.status" 501 "$code"
  scn_assert_jq "$label.code" .error.code endpoint_not_configured
}

# org_config_with_fi_rule [eval_id] [base_url]: the org's config, managed models
# on, with a block-action FutureAGI check whose evaluation API is the mock (by
# default as http://localhost:<port>, the listed name); with no eval id the
# check has none.
org_config_with_fi_rule() {
  python3 -I - "${2:-$SCN_MOCK_LISTED/fi}" "${1:-}" <<'PY'
import json, sys
base_url, eval_id = sys.argv[1], sys.argv[2]
rule = {"provider": "futureagi", "api_key": "scn-test-fi-key", "secret_key": "scn-test-fi-secret",
        "base_url": base_url, "call_type": "protect"}
if eval_id:
    rule["eval_id"] = eval_id
print(json.dumps({
    "managed_models": {"enabled": True},
    "guardrails": {"checks": {"futureagi-eval": {
        "enabled": True, "action": "block", "confidence_threshold": 0.8, "config": rule}}},
}))
PY
}

# eval_calls: how many requests the mock got on the evaluation API's path.
eval_calls() { scn_received 0 "$EVAL_PATH" | jq '.data | length'; }

scn_up
scn_grant "$SCN_ORG" 5
SCN_MOCK_LISTED=${SCN_MOCK/127.0.0.1/localhost}

scn_check "1. no video provider: every /v1/videos route is 501 endpoint_not_configured"
assert_not_configured 1.submit POST /v1/videos '{"model":"mock/ok-1","prompt":"a private video prompt"}'
assert_not_configured 1.list GET /v1/videos
assert_not_configured 1.get GET /v1/videos/video-any
assert_not_configured 1.delete DELETE /v1/videos/video-any

scn_check "2. every /v1/scheduled route is 501 endpoint_not_configured"
assert_not_configured 2.submit POST /v1/scheduled \
  '{"delay":"1m","request":{"model":"mock/ok-1","messages":[{"role":"user","content":"hi"}]}}'
assert_not_configured 2.list GET /v1/scheduled
assert_not_configured 2.get GET /v1/scheduled/sched-any
assert_not_configured 2.cancel DELETE /v1/scheduled/sched-any

scn_check "3. control: with no guardrail the org's chat passes"
scn_org_config "$SCN_ORG" '{"managed_models":{"enabled":true}}'
scn_assert_eq 3.status 200 "$(scn_chat "$CHAT")"

scn_check "4. a FutureAGI rule with no eval_id blocks, and calls nothing"
scn_org_config "$SCN_ORG" "$(org_config_with_fi_rule)"
scn_assert_eq 4.status 403 "$(scn_chat "$CHAT")"
scn_assert_jq 4.code .error.code content_blocked
scn_assert_jq 4.names_rule '.error.message | contains("futureagi-eval")' true
scn_assert_jq 4.names_field '.error.message | contains("eval_id")' true
scn_assert_jq 4.fails_closed '.error.message | contains("skipping")' false
scn_assert_eq 4.eval_calls 0 "$(eval_calls)"

scn_check "5. control: with an eval id the rule calls the evaluation API, and its failure blocks"
scn_org_config "$SCN_ORG" "$(org_config_with_fi_rule 15)"
scn_assert_eq 5.status 403 "$(scn_chat "$CHAT")"
scn_assert_jq 5.code .error.code content_blocked
scn_assert_eq 5.eval_calls 1 "$(eval_calls)"

scn_check "6. an org FutureAGI rule at an unlisted loopback address is refused, and calls nothing"
scn_org_config "$SCN_ORG" "$(org_config_with_fi_rule 15 "$SCN_MOCK/fi")"
scn_assert_eq 6.status 403 "$(scn_chat "$CHAT")"
scn_assert_jq 6.code .error.code content_blocked
scn_assert_eq 6.eval_calls 1 "$(eval_calls)"

scn_pass
