#!/usr/bin/env bash
# Gate check: a log_content=none key on a managed call logs no bodies, and
# the call still settles.
# Config: the content-mode scenario config (fixtures/content-mode/gateway.yaml:
# base.yaml with request bodies captured, so only the content mode keeps them out).
#  1. A key of the opted-in org whose metadata says log_content=none makes a
#     managed call: 200, billed; the org's outbox gains one spend record,
#     spent grows by the billed amount and nothing stays held.
#  2. Its log entry says content_mode none and has no request_body,
#     response_body or request_headers, and the prompt appears nowhere in it;
#     the attempt still carries its billed cost.
#  3. Control: a key of the same org with no log_content logs the prompt.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<n> bash gates/content_mode_none.sh
set -u
GATES_DIR=$(cd "$(dirname "$0")" && pwd)
SCN_CONFIG_TEMPLATE=$GATES_DIR/../fixtures/content-mode/gateway.yaml
source "$GATES_DIR/lib/managed_gate_common.sh"
scn_up

ORG=$SCN_ORG
PROMPT="gate private prompt words"
BODY=$(jq -nc --arg p "$PROMPT" '{model: "anthropic/claude-sonnet-5.5", max_tokens: 64, messages: [{role: "user", content: $p}]}')
usd_to_micros() { python3 -I -c 'import sys; from decimal import Decimal; print(int(Decimal(sys.argv[1]) * 1000000))' "$1"; }
kept() { jq -r --arg f "$1" 'if .[$f] == null then "no" else "yes" end' <"$SCN_DIR/entry.json"; }

scn_org_config "$ORG" '{"managed_models":{"enabled":true}}'
scn_grant "$ORG" 5
NONE_KEY=$(scn_key "$ORG" '{"metadata":{"log_content":"none"}}')

scn_check "1. a managed call on a log_content=none key is billed and settled once"
out0=$(managed_outbox "$ORG"); spent0=$(managed_credit_field "$ORG" spent_micros)
scn_assert_eq 1.status 200 "$(SCN_KEY=$NONE_KEY scn_chat "$BODY")"
scn_assert_eq 1.billing_mode managed "$(scn_hdr x-agentcc-billing-mode)"
billed=$(usd_to_micros "$(scn_hdr x-agentcc-cost)")
REQ=$(scn_hdr x-agentcc-request-id)
scn_assert_eq 1.billed_positive true "$([ "$billed" -gt 0 ] && echo true || echo false)"
scn_assert_eq 1.one_spend_record "$((out0 + 1))" "$(managed_outbox "$ORG")"
scn_assert_eq 1.spent_grows "$((spent0 + billed))" "$(managed_credit_field "$ORG" spent_micros)"
scn_assert_eq 1.nothing_held 0 "$(managed_credit_field "$ORG" held_micros)"

scn_check "2. its log entry keeps no content"
managed_entry "$REQ"
scn_assert_jq 2.content_mode .content_mode none "$SCN_DIR/entry.json"
scn_assert_eq 2.no_request_body no "$(kept request_body)"
scn_assert_eq 2.no_response_body no "$(kept response_body)"
scn_assert_eq 2.no_request_headers no "$(kept request_headers)"
scn_assert_eq 2.prompt_nowhere 0 "$(grep -c "$PROMPT" "$SCN_DIR/entry.json")"
scn_assert_jq 2.attempt_billed '.attempts[0].billed_cost_micros' "$billed" "$SCN_DIR/entry.json"

scn_check "3. control: a key with no log_content logs the prompt"
scn_assert_eq 3.status 200 "$(scn_chat "$BODY")"
managed_entry "$(scn_hdr x-agentcc-request-id)"
scn_assert_jq 3.content_mode .content_mode full "$SCN_DIR/entry.json"
scn_assert_jq 3.prompt_logged '.request_body.messages[0].content' "$PROMPT" "$SCN_DIR/entry.json"

scn_pass
