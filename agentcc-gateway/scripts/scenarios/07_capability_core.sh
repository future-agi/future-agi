#!/usr/bin/env bash
# The capability core through the real binary, on the capability mock
# (cmd/agentcc-capmock, lib_cap.sh), with the test capability echo at
# POST /v1/capabilities/echo. tools.catalog_dir names the scenario's fixture
# cards, echo-a and echo-b at $0.001 a call.
#  1. Managed: with $0.01 granted, a managed echo settles 1,050 micros per call
#     ($0.001 with the 5% tool markup), and the 10th call is refused with
#     the credits 402 (550 micros left, under the 1,050 hold).
#  2. BYOK: the same call on the org's own echo key is free and adds nothing
#     to mc:{org-acme}:outbox (outbox_pending and spent unchanged).
#  3. Fallback: a status:503 on echo-a falls back to echo-b, and the request's
#     log entry in /__logs carries the two attempts.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> bash scripts/scenarios/07_capability_core.sh
set -u
source "$(dirname "$0")/lib.sh"
source "$(dirname "$0")/lib_cap.sh"

scn_cap_up
# echo-a and echo-b are managed operator providers (the managed credential),
# so the gateway also lists them as managed model catalogs, which the
# capability mock does not serve: the catalog wait skips them.
SCN_CONFIG_TEMPLATE="$SCN_ROOT/fixtures/capability-echo/gateway.yaml" SCN_CATALOG_WAIT_SKIP=echo-a,echo-b scn_up

ORG=$SCN_ORG
echo_call() { scn_post /v1/capabilities/echo "$1"; }
sha256() { printf 'sha256:%s' "$(printf '%s' "$1" | shasum -a 256 | cut -d' ' -f1)"; }
credits() { # field of GET /-/orgs/<org>/credits
  local code
  code=$(scn_admin GET "/-/orgs/$ORG/credits")
  [ "$code" = 200 ] || scn_fail credits "GET credits returned HTTP $code: $(cat "$SCN_DIR/last.body")"
  scn_json "d[\"$1\"]"
}
org_config() { # <echo providers json> <managed true|false> <byok vendors json>
  scn_org_config "$ORG" "$(python3 -I -c '
import json, sys
order, managed, byok = json.loads(sys.argv[1]), sys.argv[2] == "true", json.loads(sys.argv[3])
cfg = {"tools": {"capabilities": {"echo": {"enabled": True, "providers": order}}},
       "mcp": {"managed_tools": {"enabled": managed, "allow": ["echo/*"]}},
       "providers": {v: {"api_key": "scn-test-org-" + v, "enabled": True} for v in byok}}
print(json.dumps(cfg))' "$1" "$2" "$3")"
}

scn_check "1. managed echo settles 1,050 micros a call; the 10th is refused 402"
org_config '["echo-a"]' true '[]'
scn_grant "$ORG" 0.01
for i in 1 2 3 4 5 6 7 8 9; do
  code=$(echo_call "{\"input\":{\"n\":$i}}")
  scn_assert_eq "1.call_${i}_status" 200 "$code"
done
scn_assert_eq 1.billing_mode managed "$(scn_hdr x-agentcc-billing-mode)"
scn_assert_eq 1.provider echo-a "$(scn_hdr x-agentcc-capability-provider)"
scn_assert_jq 1.charged_usd .usage.charged_usd 0.00105
scn_assert_jq 1.cost_source .usage.cost_source rate_card
scn_assert_eq 1.spent_micros 9450 "$(credits spent_micros)"
scn_assert_eq 1.held_micros 0 "$(credits held_micros)"
scn_assert_eq 1.outbox_pending 9 "$(credits outbox_pending)"
code=$(echo_call '{"input":{"n":10}}')
scn_assert_eq 1.tenth_status 402 "$code"
code10=$(scn_json 'd["error"]["code"]')
case "$code10" in
  managed_credits_insufficient|managed_credits_exhausted) scn_assert_eq 1.tenth_code "$code10" "$code10" ;;
  *) scn_fail 1.tenth_code "expected a credits 402, got $code10: $(cat "$SCN_DIR/last.body")" ;;
esac
scn_assert_jq 1.tenth_limit_source .error.metadata.limit_source agentcc_credits
scn_assert_eq 1.vendor_calls 9 "$(scn_cap_received echo-a | jq '.received | length')"
scn_assert_eq 1.managed_key "$(sha256 'Bearer scn-test-managed-echo-a')" "$(scn_cap_received echo-a 1 | jq -r '.received[0].headers.authorization')"
scn_assert_eq 1.spent_after_refusal 9450 "$(credits spent_micros)"

scn_check "2. the org's own echo key is free and writes no outbox record"
org_config '["echo-a"]' true '["echo-a"]'
code=$(echo_call '{"input":{"byok":true}}')
scn_assert_eq 2.status 200 "$code"
scn_assert_eq 2.billing_mode byok "$(scn_hdr x-agentcc-billing-mode)"
scn_assert_jq 2.charged_usd .usage.charged_usd 0
scn_assert_eq 2.outbox_pending 9 "$(credits outbox_pending)"
scn_assert_eq 2.spent_micros 9450 "$(credits spent_micros)"
scn_assert_eq 2.org_key "$(sha256 'Bearer scn-test-org-echo-a')" "$(scn_cap_received echo-a 1 | jq -r '.received[0].headers.authorization')"

scn_check "3. a 503 on echo-a falls back to echo-b with two attempts"
org_config '["echo-a","echo-b"]' false '["echo-a","echo-b"]'
scn_cap_arm echo-a status:503 1
code=$(echo_call '{"input":{"fallback":true}}')
scn_assert_eq 3.status 200 "$code"
scn_assert_eq 3.provider echo-b "$(scn_hdr x-agentcc-capability-provider)"
rid=$(scn_hdr x-agentcc-request-id)
entry=null
deadline=$(( $(date +%s) + 30 )) # the gateway flushes request logs every 5 s
while [ "$entry" = null ] && [ "$(date +%s)" -lt "$deadline" ]; do
  entry=$(curl -sS "$SCN_MOCK/__logs" | jq -c --arg rid "$rid" '[.logs[] | select(.request_id == $rid)][0]')
  [ "$entry" = null ] && sleep 0.5
done
[ "$entry" != null ] || scn_fail 3.entry "no log entry for request $rid in /__logs"
scn_assert_eq 3.attempts 2 "$(echo "$entry" | jq '.attempts | length')"
scn_assert_eq 3.first "echo-a error 503" "$(echo "$entry" | jq -r '.attempts[0] | "\(.provider) \(.outcome) \(.status_code)"')"
scn_assert_eq 3.second "echo-b success failover" "$(echo "$entry" | jq -r '.attempts[1] | "\(.provider) \(.outcome) \(.kind)"')"
scn_assert_eq 3.org "$ORG" "$(echo "$entry" | jq -r .metadata.org_id)"

scn_pass
