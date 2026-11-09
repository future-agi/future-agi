#!/usr/bin/env bash
# W1 gate check (plan section 3, v2 coverage fix BPC#6, C5): the strategies
# (G5) read their inputs from the other W1 lanes on the real binary.
#  1. cost_optimized ranks three targets by a catalog-only price that G4's
#     wiring_catalog supplies: w1/cheapest has targets serving
#     w1vendor/tier-high, tier-mid and tier-low (priorities 0, 1, 2; no model
#     database prices them), and every request goes to tier-low.
#  2. adaptive sees G1's per-model health after 25 failures on one model: two
#     targets of one provider serve mock/fail-503 and mock/ok-1, so the
#     provider-level signals cannot tell them apart. 25 calls fail on
#     mock/fail-503 (503, the node's own credentials, so they feed health)
#     and 5 succeed on mock/ok-1; then all 20 requests to w1/adaptive go to
#     mock/ok-1. Without per-model health the two tie and alternate.
# Both use an internal key: customer keys never select through the gateway
# router (G5 IR-8).
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<n> bash gates/W1_strategy_inputs.sh
set -u
GATES_DIR=$(cd "$(dirname "$0")" && pwd)
SCN_CONFIG_TEMPLATE=$GATES_DIR/fixtures/W1/gateway.yaml
SCN_MOCK_FIXTURES=$GATES_DIR/fixtures/W1/mock
source "$GATES_DIR/lib/w1_common.sh"
SCN_KEY=$W1_INTERNAL_KEY
scn_up

# received_models <n>: the models of the last n chat calls the mock received.
received_models() { scn_received "$1" /chat/completions | jq -r '[.data[].body.model] | join(",")'; }
chat_model() { jq -nc --arg m "$1" '{model: $m, messages: [{role: "user", content: "hi"}]}'; }

scn_check "1. cost_optimized picks the cheapest of three targets by catalog price"
scn_assert_eq 1.not_in_modeldb 0 "$(jq '[keys[] | select(test("tier-(high|mid|low)"))] | length' "$SCN_GATEWAY_DIR/internal/modeldb/litellm.json")"
for i in 1 2 3 4 5 6; do
  scn_assert_eq "1.$i.status" 200 "$(scn_chat "$(chat_model w1/cheapest)")"
done
scn_assert_eq 1.all_to_tier_low "$(printf 'w1vendor/tier-low,%.0s' 1 2 3 4 5 | sed 's/$/w1vendor\/tier-low/')" "$(received_models 6)"

scn_check "2. adaptive avoids the model whose per-model health is failing"
scn_down
SCN_CONFIG_TEMPLATE=$GATES_DIR/fixtures/W1/gateway-adaptive.yaml
scn_up
failed=0
for _ in $(seq 25); do
  [ "$(scn_chat "$(chat_model mock/fail-503)")" != 200 ] && failed=$((failed + 1))
done
scn_assert_eq 2.failures 25 "$failed"
scn_assert_jq 2.upstream_5xx .error.code 503
scn_assert_eq 2.failed_upstream_calls 25 "$(scn_received 0 /chat/completions | jq '[.data[] | select(.body.model == "mock/fail-503")] | length')"
for i in 1 2 3 4 5; do
  scn_assert_eq "2.ok.$i" 200 "$(scn_chat "$(chat_model mock/ok-1)")"
done
served=0
for _ in $(seq 20); do
  [ "$(scn_chat "$(chat_model w1/adaptive)")" = 200 ] && served=$((served + 1))
done
scn_assert_eq 2.adaptive_served 20 "$served"
scn_assert_eq 2.adaptive_to_healthy_model 20 "$(scn_received 20 /chat/completions | jq '[.data[] | select(.body.model == "mock/ok-1")] | length')"

scn_pass
