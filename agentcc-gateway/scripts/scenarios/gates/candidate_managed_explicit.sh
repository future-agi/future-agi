#!/usr/bin/env bash
# Gate check: the managed decision for rollout-candidate traffic comes from
# the effective config before the call is made billable.
#  1. Stable leaves managed models off; a rollout whose candidate alone
#     enables managed_models runs at 100% (sticky_by request). An explicit
#     managed model (not auto) is served managed, and the gateway holds and settles it:
#     one more outbox spend record, spent grows by the billed amount, nothing
#     stays held.
#  2. The same rollout at 0%: the call is 403, the upstream is not called, and
#     nothing is held or settled.
#  3. Stable allows the model; a 100% candidate denies its vendor: the call is
#     403 with no upstream call and no hold.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<n> bash gates/candidate_managed_explicit.sh
set -u
GATES_DIR=$(cd "$(dirname "$0")" && pwd)
source "$GATES_DIR/lib/managed_gate_common.sh"
scn_up

ORG=$SCN_ORG
MODEL=anthropic/claude-sonnet-5.5
CALL=$(jq -nc --arg m "$MODEL" '{model: $m, max_tokens: 64, messages: [{role: "user", content: "hi"}]}')

# config <stable managed_models json> <percent> <candidate managed_models json>
config() {
  jq -nc --argjson stable "$1" --argjson p "$2" --argjson cand "$3" '{
    managed_models: $stable,
    meta: {version: 7, config_id: "gate-candidate", pushed_at: "2026-10-16T00:00:00Z"},
    routing: {rollout: {version: "v8", percent: $p, sticky_by: "request",
      started_at: "2026-10-16T00:00:00Z", routing: {}, managed_models: $cand}}}'
}
usd_to_micros() { python3 -I -c 'import sys; from decimal import Decimal; print(int(Decimal(sys.argv[1]) * 1000000))' "$1"; }

scn_grant "$ORG" 5

scn_check "1. a candidate-only opt-in at 100%: the explicit model is served managed, held and settled once"
scn_org_config "$ORG" "$(config '{"enabled":false}' 100 '{"enabled":true}')"
out0=$(managed_outbox "$ORG"); spent0=$(managed_credit_field "$ORG" spent_micros)
scn_assert_eq 1.status 200 "$(scn_chat "$CALL")"
scn_assert_eq 1.billing_mode managed "$(scn_hdr x-agentcc-billing-mode)"
billed=$(usd_to_micros "$(scn_hdr x-agentcc-cost)")
req_id=$(scn_hdr x-agentcc-request-id) # before the next admin call replaces last.headers
scn_assert_eq 1.billed_positive true "$([ "$billed" -gt 0 ] && echo true || echo false)"
scn_assert_eq 1.one_spend_record "$((out0 + 1))" "$(managed_outbox "$ORG")"
scn_assert_eq 1.spent_grows "$((spent0 + billed))" "$(managed_credit_field "$ORG" spent_micros)"
scn_assert_eq 1.nothing_held 0 "$(managed_credit_field "$ORG" held_micros)"
managed_entry "$req_id"
scn_assert_jq 1.logged_candidate '.routing.policy_variant' candidate "$SCN_DIR/entry.json"

scn_check "2. the same rollout at 0%: 403, no upstream call, no hold"
scn_org_config "$ORG" "$(config '{"enabled":false}' 0 '{"enabled":true}')"
out0=$(managed_outbox "$ORG"); calls0=$(managed_upstream_chats)
scn_assert_eq 2.status 403 "$(scn_chat "$CALL")"
scn_assert_eq 2.upstream_not_called "$calls0" "$(managed_upstream_chats)"
scn_assert_eq 2.no_record "$out0" "$(managed_outbox "$ORG")"
scn_assert_eq 2.nothing_held 0 "$(managed_credit_field "$ORG" held_micros)"

scn_check "3. stable allows the model, a 100% candidate denies its vendor: 403"
scn_org_config "$ORG" "$(config '{"enabled":true}' 100 '{"enabled":true,"deny_vendors":["anthropic"]}')"
out0=$(managed_outbox "$ORG"); calls0=$(managed_upstream_chats)
scn_assert_eq 3.status 403 "$(scn_chat "$CALL")"
scn_assert_jq 3.code .error.code model_not_permitted
scn_assert_eq 3.upstream_not_called "$calls0" "$(managed_upstream_chats)"
scn_assert_eq 3.no_record "$out0" "$(managed_outbox "$ORG")"
scn_assert_eq 3.nothing_held 0 "$(managed_credit_field "$ORG" held_micros)"

scn_pass
