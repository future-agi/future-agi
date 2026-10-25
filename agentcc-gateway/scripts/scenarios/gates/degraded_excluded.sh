#!/usr/bin/env bash
# Gate check: a failing model is marked degraded from this node's per-model
# health, and eligibility then keeps auto off it.
# Mock fixture: the signals scenario's (fixtures/signals/mock: mock/ok-1 and mock/fail-500, both
# managed through openrouter at the same price; the mock fails every call to
# mock/fail-500 with 500).
#  1. Before: mock/fail-500 is active, and auto over [mock/fail-500] alone
#     still picks it (the call fails upstream).
#  2. 25 failed managed calls to mock/fail-500 mark it degraded in
#     GET /-/catalog (state_reason low_success_rate); mock/ok-1 stays active.
#  3. After: auto over [mock/fail-500] alone is 400 no_auto_candidate naming
#     model_degraded, with no upstream call; auto over both picks mock/ok-1,
#     and the decision rejects mock/fail-500 as model_degraded.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<n> bash gates/degraded_excluded.sh
set -u
GATES_DIR=$(cd "$(dirname "$0")" && pwd)
SCN_MOCK_FIXTURES=$GATES_DIR/../fixtures/signals/mock
source "$GATES_DIR/lib/managed_gate_common.sh"
scn_up

ORG=$SCN_ORG
AUTO='{"model":"auto","messages":[{"role":"user","content":"hi"}]}'
FAILING='{"model":"mock/fail-500","messages":[{"role":"user","content":"hi"}]}'
candidates() { jq -nc --argjson c "$1" '{managed_models: {enabled: true}, routing: {auto: {candidates: $c}}}'; }
catalog_field() { # <model> <field>: a field of the model in the last GET /-/catalog
  jq -r --arg m "$1" --arg f "$2" '[.models[] | select(.id == $m)] | first | .[$f] // ""' <"$SCN_DIR/last.body"
}
fail500_calls() { scn_received 0 /chat/completions | jq '[.data[] | select(.body.model == "mock/fail-500")] | length'; }

scn_grant "$ORG" 5

scn_check "1. before: mock/fail-500 is active and auto still picks it"
scn_assert_eq 1.catalog 200 "$(scn_admin GET /-/catalog)"
scn_assert_eq 1.state_active active "$(catalog_field mock/fail-500 state)"
scn_org_config "$ORG" "$(candidates '["mock/fail-500"]')"
before=$(fail500_calls)
code=$(scn_chat "$AUTO")
scn_assert_eq 1.auto_chose_it true "$([ "$(fail500_calls)" -gt "$before" ] && echo true || echo false)"
is_5xx=false
[ "${code:0:1}" = 5 ] && is_5xx=true
scn_assert_eq 1.failed_upstream true "$is_5xx"

scn_check "2. 25 failed calls mark mock/fail-500 degraded"
scn_org_config "$ORG" '{"managed_models":{"enabled":true}}'
for _ in $(seq 25); do scn_chat "$FAILING" >/dev/null; done
scn_assert_eq 2.catalog 200 "$(scn_admin GET /-/catalog)"
scn_assert_eq 2.state_degraded degraded "$(catalog_field mock/fail-500 state)"
scn_assert_eq 2.state_reason low_success_rate "$(catalog_field mock/fail-500 state_reason)"
scn_assert_eq 2.healthy_active active "$(catalog_field mock/ok-1 state)"

scn_check "3. after: auto never picks the degraded model"
scn_org_config "$ORG" "$(candidates '["mock/fail-500"]')"
before=$(fail500_calls)
scn_assert_eq 3.alone_status 400 "$(scn_chat "$AUTO")"
scn_assert_jq 3.alone_code .error.code no_auto_candidate
scn_assert_jq 3.alone_names_degraded '.error.message | contains("model_degraded")' true
scn_assert_eq 3.alone_not_called "$before" "$(fail500_calls)"
scn_org_config "$ORG" "$(candidates '["mock/fail-500","mock/ok-1"]')"
scn_assert_eq 3.both_status 200 "$(scn_chat "$AUTO")"
REQ=$(scn_hdr x-agentcc-request-id)
scn_assert_eq 3.both_picks_healthy mock/ok-1 "$(scn_hdr x-agentcc-auto-model)"
scn_assert_eq 3.both_not_called "$before" "$(fail500_calls)"
managed_entry "$REQ"
scn_assert_eq 3.decision_rejects_degraded true "$(jq -r '.metadata.auto_decision | fromjson | [.rejected[] | select(.model == "mock/fail-500" and .reason == "model_degraded")] | length > 0' <"$SCN_DIR/entry.json")"

scn_pass
