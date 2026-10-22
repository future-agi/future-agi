#!/usr/bin/env bash
# Gate check: Anthropic cache tokens are mapped and priced per token class,
# to the exact upstream micros.
#  An org's own key on the anthropic-direct provider (Anthropic's Messages
#  API on the mock) calls claude-sonnet-4-5; the mock answers in Anthropic's
#  shape with 1200 prompt tokens of which 600 are cache reads and 200 cache
#  writes (input_tokens 400, exclusive) and 300 output tokens. The gateway
#  maps that to prompt_tokens 1200 (inclusive), cached_tokens 600,
#  cache_write_tokens 200, and prices each class at the model database's
#  prices for the model, read here straight from internal/modeldb/litellm.json:
#    400*input + 600*cache_read + 200*cache_write + 300*output
#  (BYOK: no markup, so billed equals upstream).
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<n> bash gates/cache_tokens_priced.sh
set -u
GATES_DIR=$(cd "$(dirname "$0")" && pwd)
SCN_CONFIG_TEMPLATE=$GATES_DIR/fixtures/catalog-priced/gateway.yaml
SCN_MOCK_FIXTURES=$GATES_DIR/fixtures/catalog-priced/mock
source "$GATES_DIR/lib/gate_common.sh"
scn_up

MODEL=claude-sonnet-4-5
ORG_KEY=scn-test-byok-anthropic # the org's own (fake) Anthropic key
EXPECTED=$(python3 -I - "$SCN_GATEWAY_DIR/internal/modeldb/litellm.json" "$MODEL" <<'PY'
import json, sys
from decimal import Decimal, ROUND_HALF_UP
m = json.load(open(sys.argv[1]))[sys.argv[2]]
p = lambda k: Decimal(str(m[k]))
usd = (400 * p("input_cost_per_token") + 600 * p("cache_read_input_token_cost")
       + 200 * p("cache_creation_input_token_cost") + 300 * p("output_cost_per_token"))
print((usd * 1000000).quantize(Decimal(1), ROUND_HALF_UP))
PY
)

scn_check "1. the org's own key on Anthropic's Messages API, with cache reads and writes"
scn_org_config "$SCN_ORG" "{\"providers\":{\"anthropic-direct\":{\"api_key\":\"$ORG_KEY\",\"enabled\":true,\"models\":[\"$MODEL\"]}}}"
BODY=$(jq -nc --arg m "$MODEL" '{model: $m, max_tokens: 64, messages: [{role: "user", content: "hi"}]}')
scn_assert_eq 1.status 200 "$(scn_chat "$BODY" -H 'x-mock-behavior: cache_read:600,cache_creation:200')"
scn_assert_eq 1.billing_mode byok "$(scn_hdr x-agentcc-billing-mode)"
scn_received 1 /messages | jq '.data[0]' >"$SCN_DIR/received.json"
scn_assert_jq 1.anthropic_upstream '.path | endswith("/v1/messages")' true "$SCN_DIR/received.json"
scn_assert_jq 1.org_key '.headers["x-api-key"]' "$ORG_KEY" "$SCN_DIR/received.json"
scn_assert_jq 1.client_prompt_inclusive .usage.prompt_tokens 1200
scn_assert_jq 1.client_cached '.usage.prompt_tokens_details.cached_tokens' 600
scn_assert_jq 1.client_cache_write '.usage.prompt_tokens_details.cache_write_tokens' 200

scn_check "2. the attempt is priced per token class: $EXPECTED micros"
gate_entry "$(scn_hdr x-agentcc-request-id)"
E=$SCN_DIR/entry.json
scn_assert_jq 2.attempts '.attempts | length' 1 "$E"
scn_assert_jq 2.provider '.attempts[0].provider' anthropic-direct "$E"
scn_assert_jq 2.credential '.attempts[0].credential_type' byok "$E"
scn_assert_jq 2.prompt_tokens '.attempts[0].prompt_tokens' 1200 "$E"
scn_assert_jq 2.cached_tokens '.attempts[0].cached_tokens' 600 "$E"
scn_assert_jq 2.cache_write_tokens '.attempts[0].cache_write_tokens' 200 "$E"
scn_assert_jq 2.completion_tokens '.attempts[0].completion_tokens' 300 "$E"
scn_assert_jq 2.cost_source '.attempts[0].cost_source' modeldb "$E"
scn_assert_jq 2.upstream_micros '.attempts[0].upstream_cost_micros' "$EXPECTED" "$E"
scn_assert_jq 2.billed_micros '.attempts[0].billed_cost_micros' "$EXPECTED" "$E"

scn_pass
