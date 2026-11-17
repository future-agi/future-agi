#!/usr/bin/env bash
# Gate check: the capability rows that do not
# need the search capability, on the real binary, the capability mock, the echo fixture cards
# (the test capability the earlier row uses in place of a search adapter, which
# comes with search) and Django's ledger with its drain on the same Redis DB:
#  1. A managed capability call (echo on the managed_partner vendor echo-a)
#     reaches the ledger drain as exactly one ledger spend entry for the request,
#     with provider=echo-a and the billed amount the outbox record carried.
#  2. The tools wiring on the live binary: with tools.enabled, a
#     PUT /-/orgs/{id}/config whose mcp.managed_tools allows a byok_only
#     vendor (echo/echo-c, a byok_only card in the gate's catalog) is 400
#     capability_managed_not_allowed, and the org's stored config is unchanged.
# The run-scope row ("one managed search and one model call in the agent budget's run
# scope") needs search: capability calls take the abud: run holds only through it, so
# it is deferred with 08_v6_search.sh.
#
#   SCN_REDIS=<host:port> SCN_REDIS_DB=<n> GATE_DJANGO_MANAGE=<manage.py wrapper> \
#     bash gates/W3b_capability_ledger_live.sh
set -u
GATES_DIR=$(cd "$(dirname "$0")" && pwd)
export SCN_CONFIG_TEMPLATE=$GATES_DIR/fixtures/W3b/capability.yaml # the capability core's, plus a byok_only echo card
source "$GATES_DIR/lib/w3_common.sh"
source "$GATES_DIR/../lib_cap.sh"
w3_need_django

scn_cap_up
SCN_CATALOG_WAIT_SKIP=echo-a,echo-b scn_up
bs_django_up
bs_org "W3b capability ledger $(date -u +%Y-%m-%dT%H:%M:%SZ)"
ORG=$BS_ORG
scn_org_config "$ORG" '{"tools":{"capabilities":{"echo":{"enabled":true,"providers":["echo-a"]}}},
  "mcp":{"managed_tools":{"enabled":true,"allow":["echo/echo-a"]}}}'
SCN_KEY=$(scn_key "$ORG")
[ -n "$SCN_KEY" ] || scn_fail key "no key created for $ORG"
bs_grant "$ORG" 1

scn_check "1. a managed echo call reaches the ledger drain as one ledger spend entry with provider=echo-a"
scn_assert_eq 1.status 200 "$(scn_post /v1/capabilities/echo '{"input":{"w3b":1}}')"
RID=$(scn_hdr x-agentcc-request-id)
scn_assert_eq 1.billing_mode managed "$(scn_hdr x-agentcc-billing-mode)"
w3_wait_outbox "$ORG" 1
OUT=$(w3_outbox "$ORG")
scn_assert_eq 1.outbox_spend "spend $RID.1 echo-a" "$(echo "$OUT" | jq -r '.[0] | "\(.kind) \(.request_id) \(.provider)"')"
BILLED=$(echo "$OUT" | jq -r '.[0].billed_micros')
bs_drain_quiesce "$ORG"
bs_django_py entries "
from accounts.models.organization import Organization
from ee.usage.models.managed_credits import ManagedCreditEntry
org = Organization.objects.get(pk=BS_ARGS[0])
rows = list(ManagedCreditEntry.objects.filter(organization=org, kind='spend').values(
    'request_id', 'provider', 'amount_micros'))
bs_out({'spends': rows})" "$ORG"
E="$SCN_DIR/bs-entries.json"
echo "  ledger: $(jq -c .spends "$E")"
scn_assert_eq 1.one_entry 1 "$(jq '.spends | length' "$E")"
scn_assert_eq 1.entry_request "$RID.1" "$(jq -r '.spends[0].request_id' "$E")"
scn_assert_eq 1.entry_provider echo-a "$(jq -r '.spends[0].provider' "$E")"
scn_assert_eq 1.entry_amount "-$BILLED" "$(jq -r '.spends[0].amount_micros' "$E")" # a spend is a debit

scn_check "2. a managed byok_only glob is refused on the wired gateway with tools.enabled"
scn_admin GET "/-/orgs/$ORG/config" >/dev/null
before=$(jq -cS '.mcp' "$SCN_DIR/last.body")
scn_assert_eq 2.refused 400 "$(scn_admin PUT "/-/orgs/$ORG/config" '{"mcp":{"managed_tools":{"enabled":true,"allow":["echo/echo-c"]}}}')"
scn_assert_jq 2.code '.error.code // .code // .error' capability_managed_not_allowed
scn_admin GET "/-/orgs/$ORG/config" >/dev/null
scn_assert_eq 2.config_unchanged "$before" "$(jq -cS '.mcp' "$SCN_DIR/last.body")"
scn_pass
