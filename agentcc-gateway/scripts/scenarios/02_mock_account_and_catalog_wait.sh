#!/usr/bin/env bash
# The mock's account routes behind the real gateway, and the catalog wait
# opt-out.
#  1. A managed call's upstream charge (and a managed stream's) shows in the
#     managed upstream key's GET /api/v1/key usage, and in no other key's.
#  2. The mock's provisioning and /__account routes answer as documented.
#  3. With a managed provider that never reports live
#     (fixtures/catalog-wait/dead-catalog.yaml), scn_up returns when
#     SCN_CATALOG_WAIT_SKIP names it (or is "all"), and fails without it.
#  4. The catalog wait, on canned GET /-/catalog bodies and a fake clock:
#     a pending provider with a missing or empty id holds the wait (it can
#     never be skipped, even by an empty id in the skip list), and the
#     failure names it "<no provider id>".
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> bash scripts/scenarios/02_mock_account_and_catalog_wait.sh
set -u
source "$(dirname "$0")/lib.sh"

MANAGED=scn-test-managed-upstream   # base.yaml's managed OpenRouter key
OPERATOR=scn-test-operator-upstream # base.yaml's operator-openai key
SONNET='{"model":"anthropic/claude-sonnet-5.5","messages":[{"role":"user","content":"hi"}]}'

# mock_call <method> <path> <bearer> [json]: call the mock directly; prints
# the HTTP status, keeps the body in $SCN_DIR/mock.body.
mock_call() {
  local args=(-sS -o "$SCN_DIR/mock.body" -w '%{http_code}' -X "$1" "$SCN_MOCK$2")
  [ -n "$3" ] && args+=(-H "Authorization: Bearer $3")
  [ $# -ge 4 ] && args+=(-H 'Content-Type: application/json' --data-binary "$4")
  curl "${args[@]}"
}

# key_usage <bearer>: the bearer's GET /api/v1/key usage, in micros.
key_usage() {
  [ "$(mock_call GET /api/v1/key "$1")" = 200 ] || { echo "GET /api/v1/key failed: $(cat "$SCN_DIR/mock.body")"; return; }
  jq '.data.usage * 1000000 | round' <"$SCN_DIR/mock.body"
}

# wait_on <catalog json> <skip list>: run lib.sh's _scn_wait_catalog against
# a canned GET /-/catalog body, on a fake clock (each poll's sleep advances it
# 1 s, so a wait that never ends fails at its 30 s deadline at once). Sets
# WAIT_STATUS (its exit status) and WAIT_LINE (its last output line).
wait_on() {
  local dir=$RUN_DIR/wait-unit
  mkdir -p "$dir"
  printf '%s' "$1" >"$dir/catalog.json"
  echo 1000 >"$dir/clock"
  (
    SCN_DIR=$dir SCN_FAIL_FILE='' SCN_CATALOG_WAIT_SKIP=$2
    scn_admin() { cp "$SCN_DIR/catalog.json" "$SCN_DIR/last.body"; echo 200; }
    date() { cat "$SCN_DIR/clock"; }
    sleep() { echo $(($(cat "$SCN_DIR/clock") + 1)) >"$SCN_DIR/clock"; }
    _scn_wait_catalog
  ) >"$dir/out" 2>&1
  WAIT_STATUS=$?
  WAIT_LINE=$(tail -n 1 "$dir/out")
}

scn_up

scn_check "1. a managed call's charge is the managed upstream key's usage, and no other key's"
# A key provisioned under the managed bearer, as per-org upstream keys are:
# the gateway does not use it, so its usage must stay 0.
scn_assert_eq 1.provisioned 201 "$(mock_call POST /api/v1/keys "$MANAGED" '{"name":"scn-provisioned-org"}')"
PROVISIONED=$(jq -r .key <"$SCN_DIR/mock.body")
HASH=$(jq -r .data.hash <"$SCN_DIR/mock.body")
scn_org_config "$SCN_ORG" '{"managed_models":{"enabled":true}}'
scn_grant "$SCN_ORG" 5
scn_assert_eq 1.no_charges_yet 0 "$(scn_charges)"
scn_assert_eq 1.status 200 "$(scn_chat "$SONNET")"
scn_assert_eq 1.billing_mode managed "$(scn_hdr x-agentcc-billing-mode)"
scn_assert_eq 1.upstream_charge_micros 5400 "$(scn_charges)"
scn_assert_eq 1.managed_key_usage 5400 "$(key_usage "$MANAGED")"
scn_assert_eq 1.stream_status 200 "$(scn_stream "$SONNET")"
scn_assert_eq 1.stream_done '[DONE]' "$(scn_json 'd[-1]')"
scn_assert_eq 1.stream_upstream_charge_micros 10800 "$(scn_charges)"
scn_assert_eq 1.managed_key_usage_is_every_charge "$(scn_charges)" "$(key_usage "$MANAGED")"
scn_assert_eq 1.operator_key_usage 0 "$(key_usage "$OPERATOR")"
scn_assert_eq 1.customer_key_usage 0 "$(key_usage "$SCN_KEY")"
scn_assert_eq 1.provisioned_key_usage 0 "$(key_usage "$PROVISIONED")"
scn_assert_eq 1.provisioned_by_hash 200 "$(mock_call GET "/api/v1/keys/$HASH" "$MANAGED")"
scn_assert_jq 1.provisioned_usage_by_hash .data.usage 0 "$SCN_DIR/mock.body"

scn_check "2. provisioning and /__account answer as documented"
scn_assert_eq 2.patch 200 "$(mock_call PATCH "/api/v1/keys/$HASH" "$MANAGED" '{"limit":2.5,"disabled":true}')"
scn_assert_jq 2.patched '[.data.limit, .data.limit_remaining, .data.disabled] | @csv' '2.5,2.5,true' "$SCN_DIR/mock.body"
scn_assert_eq 2.other_bearer_404 404 "$(mock_call GET "/api/v1/keys/$HASH" "$OPERATOR")"
scn_assert_eq 2.no_bearer_401 401 "$(mock_call GET /api/v1/key "")"
scn_assert_eq 2.delete 200 "$(mock_call DELETE "/api/v1/keys/$HASH" "$MANAGED")"
scn_assert_eq 2.deleted_404 404 "$(mock_call GET "/api/v1/keys/$HASH" "$MANAGED")"
scn_assert_eq 2.account 200 "$(mock_call POST /__account "" '{"balance_usd": 42}')"
scn_assert_eq 2.credits 200 "$(mock_call GET /api/v1/credits "$MANAGED")"
scn_assert_jq 2.credits_balance '(.data.total_credits - .data.total_usage) * 1000000 | round' 42000000 "$SCN_DIR/mock.body"
scn_assert_jq 2.credits_usage_kept '.data.total_usage * 1000000 | round' 10800 "$SCN_DIR/mock.body"
scn_down

scn_check "3. scn_up waits for every managed catalog unless SCN_CATALOG_WAIT_SKIP names it"
DEAD_TEMPLATE=$SCN_ROOT/fixtures/catalog-wait/dead-catalog.yaml
RUN_DIR=$SCN_DIR
SCN_SKIP_BUILD=1
scn_assert_eq 3.dead_port_refuses 7 "$(curl -s -o /dev/null --max-time 2 http://127.0.0.1:1/; echo $?)"

SCN_DIR=$RUN_DIR/skip-one
SCN_CONFIG_TEMPLATE=$DEAD_TEMPLATE SCN_CATALOG_WAIT_SKIP='no-such-provider, openrouter-dead' scn_up
scn_assert_eq 3.skip_one_catalog 200 "$(scn_admin GET /-/catalog)"
scn_assert_jq 3.live_provider_was_waited_for '.providers[] | select(.provider == "openrouter") | .source' live
scn_assert_jq 3.skipped_provider_stays_bundled '.providers[] | select(.provider == "openrouter-dead") | .source' bundled
scn_down

SCN_DIR=$RUN_DIR/skip-all
SCN_CONFIG_TEMPLATE=$DEAD_TEMPLATE SCN_CATALOG_WAIT_SKIP=all scn_up
scn_assert_eq 3.skip_all_healthy 200 "$(curl -sS -o /dev/null -w '%{http_code}' "$SCN_GW/healthz")"
scn_down

SCN_DIR=$RUN_DIR/no-skip
started=$(date +%s)
out=$( (SCN_FAIL_FILE='' SCN_CATALOG_WAIT_SKIP='' SCN_CONFIG_TEMPLATE=$DEAD_TEMPLATE && scn_up) 2>&1)
status=$?
echo "$out" | tail -n 1 | cut -c 1-160 | sed 's/^/  | /'
scn_assert_eq 3.no_skip_fails 1 "$status"
scn_assert_eq 3.no_skip_names_the_provider 1 \
  "$(echo "$out" | grep -c 'SCN FAIL \[scn_up\]: managed catalog never loaded from the mock for openrouter-dead ')"
scn_assert_eq 3.no_skip_waited_the_deadline yes "$( [ $(( $(date +%s) - started )) -ge 30 ] && echo yes || echo no)"
SCN_DIR=$RUN_DIR

scn_check "4. the catalog wait counts pending providers; one without an id holds it"
LIVE='{"provider":"openrouter","source":"live"}'
wait_on "{\"providers\":[$LIVE]}" ''
scn_assert_eq 4.all_live_returns 0 "$WAIT_STATUS"
wait_on "{\"providers\":[$LIVE,{\"provider\":\"dead\",\"source\":\"bundled\"}]}" 'dead'
scn_assert_eq 4.skipped_named_provider_returns 0 "$WAIT_STATUS"
wait_on "{\"providers\":[$LIVE,{\"source\":\"bundled\"}]}" 'dead,, '
scn_assert_eq 4.missing_id_holds_the_wait 1 "$WAIT_STATUS"
scn_assert_eq 4.missing_id_named 1 \
  "$(echo "$WAIT_LINE" | grep -c 'never loaded from the mock for <no provider id> (SCN_CATALOG_WAIT_SKIP')"
wait_on "{\"providers\":[$LIVE,{\"provider\":\"\",\"source\":\"bundled\"},{\"provider\":\"dead\",\"source\":\"bundled\"}]}" 'dead,,'
scn_assert_eq 4.empty_id_holds_the_wait 1 "$WAIT_STATUS"
scn_assert_eq 4.empty_id_named 1 \
  "$(echo "$WAIT_LINE" | grep -c 'never loaded from the mock for <no provider id> (SCN_CATALOG_WAIT_SKIP')"
scn_assert_eq 4.waited_on_the_fake_clock 1030 "$(cat "$RUN_DIR/wait-unit/clock")"

scn_pass
