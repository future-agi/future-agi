#!/usr/bin/env bash
# Gate check: a model priced
# only through the pricer's `priced` (installed by wiring_pricing, read by the capabilities matrix through
# Handlers.deps) shows managed_allowed=true in /-/capabilities; an unpriced
# one shows false.
#  1. w1vendor/catalog-only is priced only by the managed catalog (no model
#     database lists it), so only the Pricer, which reads the catalog-first
#     lookup wiring_catalog injects, prices it: the default `priced` reads the model
#     database alone and would say false.
#  2. w1vendor/unpriced has a dynamic (negative) catalog price: false.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<n> bash gates/W1_priced_capabilities.sh
set -u
GATES_DIR=$(cd "$(dirname "$0")" && pwd)
SCN_CONFIG_TEMPLATE=$GATES_DIR/fixtures/W1/gateway.yaml
SCN_MOCK_FIXTURES=$GATES_DIR/fixtures/W1/mock
source "$GATES_DIR/lib/w1_common.sh"
scn_up

CELL='.providers.openrouter.endpoints["chat.completions"]'
scn_check "0. both models are in the managed catalog; neither is in the model database"
scn_assert_eq 0.not_in_modeldb 0 "$(jq '[keys[] | select(test("w1vendor|catalog-only|/unpriced$"))] | length' "$SCN_GATEWAY_DIR/internal/modeldb/litellm.json")"

scn_check "1. a model priced only through the pricer's priced: managed_allowed true"
scn_assert_eq 1.status 200 "$(scn_admin GET '/-/capabilities?model=w1vendor/catalog-only')"
scn_assert_jq 1.model .model w1vendor/catalog-only
scn_assert_jq 1.credential_type '.providers.openrouter.credential_type' managed
scn_assert_jq 1.chat_status "$CELL.status" supported
scn_assert_jq 1.managed_allowed "$CELL.managed_allowed" true

scn_check "2. an unpriced model: managed_allowed false"
scn_assert_eq 2.status 200 "$(scn_admin GET '/-/capabilities?model=w1vendor/unpriced')"
scn_assert_jq 2.model .model w1vendor/unpriced
scn_assert_jq 2.chat_status "$CELL.status" supported
scn_assert_jq 2.managed_allowed "$CELL.managed_allowed" false

scn_pass
