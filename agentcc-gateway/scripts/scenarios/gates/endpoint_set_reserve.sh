#!/usr/bin/env bash
# Gate check: the endpoint set reaches the credit hold. The hold is
# taken at the highest price in the set, and the upstream request pins exactly
# that set in provider.order and provider.only.
# Fixture (fixtures/sources/endpoint-set/mock): the eligibility mock, where
# anthropic/claude-sonnet-5.5 is listed at $3/$15 per M and has OpenRouter
# endpoints deepinfra ($2.5/$12), anthropic and google-vertex ($3/$15), plus a
# together endpoint at $4/$20, dearer than the model's own listing.
#  1. A plain managed call: while the upstream is held back (latency:3000),
#     GET /-/orgs/{org}/credits shows held_micros equal to the worst-case
#     estimate at together's card, not at the model's own; /__received shows
#     order == only == the four endpoints; the call settles and nothing stays
#     held.
#  2. With provider.only [deepinfra] the set is deepinfra alone: the hold is
#     at deepinfra's card (below the model's own), and order == only ==
#     [deepinfra].
# Each expected hold is (8 input tokens x prompt + 380 x completion) x 1.05,
# rounded up to a whole micro (billing.EstimateWorstCaseMicros; "hi" is 8
# tokens, as in 15_credits.sh).
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<n> bash gates/endpoint_set_reserve.sh
set -u
GATES_DIR=$(cd "$(dirname "$0")" && pwd)
SCN_MOCK_FIXTURES=$GATES_DIR/fixtures/sources/endpoint-set/mock
source "$GATES_DIR/lib/managed_gate_common.sh"
scn_up

ORG=$SCN_ORG
MODEL=anthropic/claude-sonnet-5.5
ENDPOINTS=$SCN_MOCK_FIXTURES/endpoints/anthropic/claude-sonnet-5.5.json

# hold_for <tag>: the worst-case hold at that endpoint's card, in micros.
hold_for() {
  python3 -I - "$ENDPOINTS" "$1" <<'PY'
import json, math, sys
from decimal import Decimal
eps = json.load(open(sys.argv[1]))["data"]["endpoints"]
p = next(e["pricing"] for e in eps if e["tag"] == sys.argv[2])
usd = 8 * Decimal(p["prompt"]) + 380 * Decimal(p["completion"])
print(math.ceil(usd * 1_000_000 * Decimal("1.05")))
PY
}

# held_during <body>: send body while the upstream is held back and print the
# highest held_micros seen; the call's status lands in $SCN_DIR/held.code.
held_during() {
  scn_behavior latency:3000 "$MODEL" >/dev/null
  curl -sS -o "$SCN_DIR/held.body" -w '%{http_code}' -X POST "$SCN_GW/v1/chat/completions" \
    -H "Authorization: Bearer $SCN_KEY" -H 'Content-Type: application/json' \
    --data-binary "$1" >"$SCN_DIR/held.code" &
  local pid=$! max=0 h i
  for i in $(seq 25); do
    sleep 0.1
    h=$(managed_credit_field "$ORG" held_micros)
    [ "$h" -gt "$max" ] 2>/dev/null && max=$h
  done
  wait "$pid"
  scn_behavior_clear >/dev/null
  echo "$max"
}
last_sent() { scn_received 1 /chat/completions | jq -c ".data[0].body | $1"; }

scn_org_config "$ORG" '{"managed_models":{"enabled":true}}'
scn_grant "$ORG" 5
scn_assert_eq setup.endpoints_status 200 "$(scn_admin GET "/-/catalog/endpoints?model=$MODEL&provider=openrouter")"
scn_assert_eq setup.endpoints_loaded 4 "$(jq '.data.endpoints | length' <"$SCN_DIR/last.body")"

scn_check "1. the hold is at the dearest card in the set, and the upstream pins the whole set"
WANT=$(hold_for together); MODEL_HOLD=$(hold_for anthropic)
scn_assert_eq 1.set_dearer_than_model true "$([ "$WANT" -gt "$MODEL_HOLD" ] && echo true || echo false)"
BODY=$(jq -nc --arg m "$MODEL" '{model: $m, max_tokens: 380, messages: [{role: "user", content: "hi"}]}')
scn_assert_eq 1.held_micros "$WANT" "$(held_during "$BODY")"
scn_assert_eq 1.status 200 "$(cat "$SCN_DIR/held.code")"
scn_assert_eq 1.order_is_only true "$(last_sent '.provider.order == .provider.only')"
scn_assert_eq 1.only_the_set '["anthropic","deepinfra","google-vertex","together"]' "$(last_sent '.provider.only | sort')"
scn_assert_eq 1.nothing_held_after 0 "$(managed_credit_field "$ORG" held_micros)"

scn_check "2. provider.only [deepinfra]: the set is deepinfra, and so is the hold"
WANT=$(hold_for deepinfra)
scn_assert_eq 2.set_cheaper_than_model true "$([ "$WANT" -lt "$MODEL_HOLD" ] && echo true || echo false)"
BODY=$(jq -nc --arg m "$MODEL" '{model: $m, max_tokens: 380, messages: [{role: "user", content: "hi"}], provider: {only: ["deepinfra"]}}')
scn_assert_eq 2.held_micros "$WANT" "$(held_during "$BODY")"
scn_assert_eq 2.status 200 "$(cat "$SCN_DIR/held.code")"
scn_assert_eq 2.order '["deepinfra"]' "$(last_sent .provider.order)"
scn_assert_eq 2.only '["deepinfra"]' "$(last_sent .provider.only)"
scn_assert_eq 2.nothing_held_after 0 "$(managed_credit_field "$ORG" held_micros)"

scn_pass
