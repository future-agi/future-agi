#!/usr/bin/env bash
# Gate check: a price-card source beats OpenRouter at equal list price
# through the landed-cost rule, and cost_landed for the direct attempt has no
# upstream-fee part (only the processing part).
# Config: the sources scenario config (fixtures/sources/config.yaml):
# OpenRouter (fee 550 bps) lists anthropic/claude-sonnet-5.5 at $2/$10 per M
# in the sources mock catalog; mock-direct (fee 0) is a price-card source. Card:
# fixtures/sources/direct-source/mock-direct.json sells the same model under the
# same id at the same $2/$10, data_collection deny.
#  1. model=auto over the model: the source choice keeps mock-direct over
#     openrouter (metadata.source_choice), and the call is served managed by
#     mock-direct, priced from the card (cost_source price_card).
#  2. That attempt's landed cost is upstream plus the processing part only:
#     upstream_fee_bps 0, landed = upstream + billed x processing_fee_bps.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<n> bash gates/direct_source_preferred.sh
set -u
GATES_DIR=$(cd "$(dirname "$0")" && pwd)
FIX=$GATES_DIR/../fixtures/sources
export SCN_RUN=${SCN_RUN:-gate-managed}
GATE_HOME=${GATE_HOME:-${TMPDIR:-/tmp}/agentcc-gates}
export SCN_HOME=${SCN_HOME:-$GATE_HOME/scn-managed}
WORK=$SCN_HOME/direct-source
mkdir -p "$WORK"
CARD=$WORK/mock-direct.json
# The card as of today, so it is never stale (scn_up empties SCN_DIR).
python3 -I - "$GATES_DIR/fixtures/sources/direct-source/mock-direct.json" "$CARD" "$(date -u +%Y-%m-%d)" <<'PY'
import json, sys
src, dst, today = sys.argv[1:]
card = json.load(open(src, encoding="utf-8"))
card["as_of"] = today
json.dump(card, open(dst, "w", encoding="utf-8"), indent=2)
PY
sed "s#@@SOURCES_CARD@@#$CARD#" "$FIX/config.yaml" >"$WORK/config.yaml"
export SCN_CONFIG_TEMPLATE=$WORK/config.yaml
export SCN_MOCK_FIXTURES=$FIX/mock
export SCN_CATALOG_WAIT_SKIP=mock-direct
source "$GATES_DIR/lib/managed_gate_common.sh"
scn_up

ORG=$SCN_ORG
MODEL=anthropic/claude-sonnet-5.5

scn_org_config "$ORG" "$(jq -nc --arg m "$MODEL" '{managed_models: {enabled: true}, routing: {auto: {candidates: [$m]}}}')"
scn_grant "$ORG" 5

scn_check "1. auto keeps the direct card source over OpenRouter at equal list price"
scn_assert_eq 1.status 200 "$(scn_chat '{"model":"auto","messages":[{"role":"user","content":"hi"}]}')"
REQ=$(scn_hdr x-agentcc-request-id)
scn_assert_eq 1.provider mock-direct "$(scn_hdr x-agentcc-provider)"
scn_assert_eq 1.billing_mode managed "$(scn_hdr x-agentcc-billing-mode)"
scn_assert_eq 1.upstream_path '"/v1/chat/completions"' "$(scn_received 1 /chat/completions | jq -c '.data[0].path')"
managed_entry "$REQ"
E=$SCN_DIR/entry.json
scn_assert_jq 1.source_choice_chosen '.metadata.source_choice | fromjson | .[0].chosen' mock-direct "$E"
scn_assert_jq 1.source_choice_over '.metadata.source_choice | fromjson | .[0].over | join(",")' openrouter "$E"
scn_assert_jq 1.attempt_provider '.attempts[0].provider' mock-direct "$E"
scn_assert_jq 1.cost_source '.attempts[0].cost_source' price_card "$E"
WANT_UP=$(python3 -I - "$CARD" "$E" <<'PY'
import json, sys
from decimal import Decimal, ROUND_HALF_UP
p = json.load(open(sys.argv[1]))["models"][0]["prices"]
a = json.load(open(sys.argv[2]))["attempts"][0]
up = Decimal(a["prompt_tokens"]) * Decimal(p["prompt"]) + Decimal(a["completion_tokens"]) * Decimal(p["completion"])
print(int((up * 10**6).quantize(Decimal(1), ROUND_HALF_UP)))
PY
)
scn_assert_jq 1.priced_from_card '.attempts[0].upstream_cost_micros' "$WANT_UP" "$E"

scn_check "2. the direct attempt's landed cost has no upstream fee, only the processing part"
scn_assert_jq 2.no_upstream_fee '.attempts[0].upstream_fee_bps // 0' 0 "$E"
read -r WANT_LANDED PF <<<"$(python3 -I - "$E" <<'PY'
import json, sys
from decimal import Decimal, ROUND_HALF_UP
a = json.load(open(sys.argv[1]))["attempts"][0]
pf = a.get("processing_fee_bps", 0)
part = int((Decimal(a["billed_cost_micros"]) * pf / 10000).quantize(Decimal(1), ROUND_HALF_UP))
print(a["upstream_cost_micros"] + part, pf)
PY
)"
scn_assert_eq 2.processing_part_present true "$([ "${PF:-0}" -gt 0 ] && echo true || echo false)"
scn_assert_jq 2.landed_is_upstream_plus_processing '.attempts[0].landed_cost_micros' "$WANT_LANDED" "$E"

scn_pass
