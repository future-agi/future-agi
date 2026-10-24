#!/usr/bin/env bash
# Gate check: with wiring_catalog's catalog-first price lookup, the gateway
# prices a model that only the managed catalog prices, with
# cost_source=catalog.
#  1. gatevendor/catalog-only is in the mock's OpenRouter catalog (prompt
#     $0.000004, completion $0.000008 per token) and in no model database.
#  2. A managed call to it (the mock reports no cost, 1200 prompt and 300
#     completion tokens) is logged with one attempt priced from the catalog:
#     cost_source catalog, upstream 1200*4 + 300*8 = 7200 micros, billed with
#     the 5% markup 7560 micros.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<n> bash gates/catalog_pricing.sh
set -u
GATES_DIR=$(cd "$(dirname "$0")" && pwd)
SCN_CONFIG_TEMPLATE=$GATES_DIR/fixtures/catalog-priced/gateway.yaml
SCN_MOCK_FIXTURES=$GATES_DIR/fixtures/catalog-priced/mock
source "$GATES_DIR/lib/gate_common.sh"
scn_up

MODEL=gatevendor/catalog-only
scn_check "1. the catalog lists $MODEL with its price; no model database does"
scn_assert_eq 1.catalog_status 200 "$(scn_admin GET /-/catalog)"
scn_assert_jq 1.catalog_source '.providers[] | select(.provider == "openrouter") | .source' live
scn_assert_eq 1.not_in_modeldb 0 "$(jq --arg m "$MODEL" '[keys[] | select(. == $m or endswith("/" + ($m | split("/")[1])))] | length' "$SCN_GATEWAY_DIR/internal/modeldb/litellm.json")"

scn_check "2. a managed call to $MODEL is priced from the catalog"
scn_org_config "$SCN_ORG" '{"managed_models":{"enabled":true}}'
scn_grant "$SCN_ORG" 5
BODY=$(jq -nc --arg m "$MODEL" '{model: $m, messages: [{role: "user", content: "hi"}]}')
scn_assert_eq 2.status 200 "$(scn_chat "$BODY")"
scn_assert_eq 2.billing_mode managed "$(scn_hdr x-agentcc-billing-mode)"
gate_entry "$(scn_hdr x-agentcc-request-id)"
E=$SCN_DIR/entry.json
scn_assert_jq 2.attempts '.attempts | length' 1 "$E"
scn_assert_jq 2.attempt_model '.attempts[0].model' "$MODEL" "$E"
scn_assert_jq 2.prompt_tokens '.attempts[0].prompt_tokens' 1200 "$E"
scn_assert_jq 2.completion_tokens '.attempts[0].completion_tokens' 300 "$E"
scn_assert_jq 2.cost_source '.attempts[0].cost_source' catalog "$E"
scn_assert_jq 2.upstream_micros '.attempts[0].upstream_cost_micros' 7200 "$E"
scn_assert_jq 2.billed_micros '.attempts[0].billed_cost_micros' 7560 "$E"

scn_pass
