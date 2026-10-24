#!/usr/bin/env bash
# 32_sources.sh: managed catalog sources through the real
# binary. Two managed sources: OpenRouter (the mock's catalog, fee 550 bps)
# and mock-direct, a direct provider priced from a price card (fee 0) and
# served by the mock's /v1/chat/completions.
#
#   1. Both sources load: OpenRouter live from the mock, mock-direct from its
#      card (source price_card, which never reports live, so scn_up's
#      catalog wait skips it).
#   2. A managed call to the card's model is served by mock-direct with the
#      markup and credential_type managed, priced from the card
#      (cost_source price_card); its log entry's cost_landed is cost_upstream
#      plus the processing part only, with no upstream fee.
#   3. /-/catalog/stats lists both sources, each counted from its own
#      fixture (no number is written in this script).
#   4. A model the org also reaches with its own key shows access=both in
#      GET /-/orgs/{org}/catalog/access, with both managed sources.
#
# Usage: SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<n> bash 32_sources.sh
#
# set -u only, like the other scenarios: lib.sh's teardown kills processes
# that may already be gone. Every step that can fail checks for it.
set -u
source "$(dirname "$0")/lib.sh"

FIX=$SCN_ROOT/fixtures/sources
DIRECT_MODEL=claude-sonnet-5-5-20260928
CANONICAL=anthropic/claude-sonnet-5.5

# The card as of today, so it is never stale, and the config pointing at it.
# Both live in the run's scratch dir (scn_up empties SCN_DIR).
WORK=$SCN_HOME/scn-sources
mkdir -p "$WORK" || scn_fail setup "cannot create $WORK"
CARD=$WORK/mock-direct.json
python3 -I - "$FIX/pricecards/mock-direct.json" "$CARD" "$(date -u +%Y-%m-%d)" <<'PY' || scn_fail setup "cannot write the card"
import json, sys
src, dst, today = sys.argv[1:]
card = json.load(open(src, encoding="utf-8"))
card["as_of"] = today
json.dump(card, open(dst, "w", encoding="utf-8"), indent=2)
PY
sed "s#@@SOURCES_CARD@@#$CARD#" "$FIX/config.yaml" >"$WORK/config.yaml" || scn_fail setup "cannot write the config"

export SCN_CONFIG_TEMPLATE=$WORK/config.yaml
export SCN_MOCK_FIXTURES=$FIX/mock
export SCN_CATALOG_WAIT_SKIP=mock-direct
scn_up

# entry <request id>: wait (30 s) for the request's log entry at the mock and
# keep it in $SCN_DIR/entry.json.
entry() {
  local deadline=$(( $(date +%s) + 30 ))
  while :; do
    curl -sS "$SCN_MOCK/__logs" | jq --arg id "$1" '[.logs[] | select(.request_id == $id)] | first // empty' >"$SCN_DIR/entry.json"
    [ -s "$SCN_DIR/entry.json" ] && return 0
    [ "$(date +%s)" -ge "$deadline" ] && scn_fail "entry $1" "no request log entry for $1"
    sleep 0.5
  done
}

scn_check "1. both sources load"
scn_assert_eq 1.catalog_status 200 "$(scn_admin GET /-/catalog)"
scn_assert_jq 1.openrouter_live '.providers[] | select(.provider == "openrouter") | .source' live
scn_assert_jq 1.direct_from_card '.providers[] | select(.provider == "mock-direct") | .source' price_card
scn_assert_jq 1.direct_model_listed "[.models[] | select(.provider == \"mock-direct\" and .id == \"$DIRECT_MODEL\")] | length" 1

scn_check "2. a managed call to the card's model: markup, managed, landed cost without an upstream fee"
scn_org_config "$SCN_ORG" '{"managed_models":{"enabled":true}}'
scn_grant "$SCN_ORG" 5
BODY=$(jq -nc --arg m "$DIRECT_MODEL" '{model: $m, messages: [{role: "user", content: "hi"}]}')
scn_assert_eq 2.status 200 "$(scn_chat "$BODY")"
scn_assert_eq 2.billing_mode managed "$(scn_hdr x-agentcc-billing-mode)"
scn_received 1 /chat/completions >"$SCN_DIR/received.json"
scn_assert_jq 2.upstream_path '.data[0].path' /v1/chat/completions "$SCN_DIR/received.json"
scn_assert_jq 2.upstream_model '.data[0].body.model' "$DIRECT_MODEL" "$SCN_DIR/received.json"
entry "$(scn_hdr x-agentcc-request-id)"
E=$SCN_DIR/entry.json
scn_assert_jq 2.credential_type '.attempts[0].credential_type' managed "$E"
scn_assert_jq 2.provider '.attempts[0].provider' mock-direct "$E"
scn_assert_jq 2.cost_source '.attempts[0].cost_source' price_card "$E"
scn_assert_jq 2.no_upstream_fee '.attempts[0].upstream_fee_bps // 0' 0 "$E"
# Every expected amount is computed from the card and the entry's own token
# counts and fee rates.
read -r WANT_UP WANT_BILLED WANT_LANDED PROCESSING_BPS <<<"$(python3 -I - "$CARD" "$DIRECT_MODEL" "$E" <<'PY'
import json, sys
from decimal import Decimal, ROUND_HALF_UP
card, model, entry = json.load(open(sys.argv[1])), sys.argv[2], json.load(open(sys.argv[3]))
prices = next(m["prices"] for m in card["models"] if m["id"] == model)
a = entry["attempts"][0]
def half_up(x):
    return int(Decimal(x).quantize(Decimal(1), ROUND_HALF_UP))
up = half_up(Decimal(a["prompt_tokens"]) * Decimal(prices["prompt"]) * 10**6
             + Decimal(a["completion_tokens"]) * Decimal(prices["completion"]) * 10**6)
billed = half_up(Decimal(up) * 10500 / 10000)
pf = a.get("processing_fee_bps", 0)
landed = up + half_up(Decimal(billed) * pf / 10000)
print(up, billed, landed, pf)
PY
)"
scn_assert_eq 2.processing_part_present true "$( [ "${PROCESSING_BPS:-0}" -gt 0 ] && echo true || echo false)"
scn_assert_jq 2.upstream_micros '.attempts[0].upstream_cost_micros' "$WANT_UP" "$E"
scn_assert_jq 2.billed_with_markup '.attempts[0].billed_cost_micros' "$WANT_BILLED" "$E"
scn_assert_jq 2.landed_micros '.attempts[0].landed_cost_micros' "$WANT_LANDED" "$E"
scn_assert_jq 2.entry_cost_upstream '((.cost_upstream * 1000000) | round)' "$WANT_UP" "$E"
scn_assert_jq 2.entry_cost_landed '((.cost_landed * 1000000) | round)' "$WANT_LANDED" "$E"

scn_check "3. /-/catalog/stats lists both sources, counted from their fixtures"
read -r OR_MODELS CARD_MODELS CARD_PRICED <<<"$(python3 -I - "$FIX/mock/models.json" "$CARD" <<'PY'
import json, sys
models, card = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
# A card model is priced when its prices bill a call (catalog.Prices.BillsCall).
bills = lambda p: any(float((p or {}).get(k) or 0) > 0 for k in ("prompt", "completion", "request"))
print(len(models["data"]), len(card["models"]), sum(1 for m in card["models"] if bills(m.get("prices"))))
PY
)"
scn_assert_eq 3.stats_status 200 "$(scn_admin GET /-/catalog/stats)"
scn_assert_jq 3.sources '.per_source | keys | join(",")' openrouter,price_card
scn_assert_jq 3.openrouter_models '.per_source.openrouter.models' "$OR_MODELS"
scn_assert_jq 3.card_models '.per_source.price_card.models' "$CARD_MODELS"
scn_assert_jq 3.card_endpoints '.per_source.price_card.endpoints' "$CARD_PRICED"
scn_assert_jq 3.card_priced_endpoints '.per_source.price_card.priced_endpoints' "$CARD_PRICED"

scn_check "4. a model the org also has through its own key shows access=both"
# The org's own Anthropic key, pointed at the mock: nothing here calls it,
# and nothing could reach a real upstream if it did.
ORG_CFG=$(jq -nc --arg mock "$SCN_MOCK" --arg m "$DIRECT_MODEL" '{managed_models: {enabled: true},
  providers: {anthropic: {api_key: "scn-test-org-anthropic", base_url: $mock, enabled: true, models: [$m]}}}')
scn_org_config "$SCN_ORG" "$ORG_CFG"
scn_assert_eq 4.access_status 200 "$(scn_admin GET "/-/orgs/$SCN_ORG/catalog/access")"
ROW=".data[] | select(.id == \"$CANONICAL\")"
scn_assert_jq 4.access_both "$ROW | .access" both
scn_assert_jq 4.byok_provider "$ROW | .byok_providers | join(\",\")" anthropic
scn_assert_jq 4.managed_sources "$ROW | .managed_sources | join(\",\")" mock-direct,openrouter
scn_assert_jq 4.managed_now "$ROW | .managed" true
scn_assert_jq 4.unpriced_card_model_not_offered '[.data[] | select(.id == "mockvendor/mock-direct-unpriced")] | length' 0
scn_assert_jq 4.stats_both '.stats.both >= 1 and .stats.canonical_models == (.data | length)' true

scn_pass
