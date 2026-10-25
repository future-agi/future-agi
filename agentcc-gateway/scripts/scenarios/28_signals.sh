#!/usr/bin/env bash
# Agent signals, through the real gateway binary and the mock upstream
# (fixtures/signals/mock: the common catalog plus mock/ok-1 and mock/fail-500,
# both managed through openrouter).
#  1. 25 requests to the managed mock/fail-500 leave it degraded in
#     GET /-/catalog (state_reason low_success_rate): this node's per-model
#     health holds 25 failed calls. mock/ok-1 stays active.
#  2. An agent call to mock/ok-1 is classified: its request log entry carries
#     metadata.outcome_class ok.
#  3. POST /v1/feedback {outcome: verify_failed} for that generation returns
#     200, and a later /__logs batch carries one kind: outcome record for it,
#     with the call's agent, run, step and model and source feedback.
#  4. Another org's key gets 404 generation_not_found for the same
#     generation, and an unknown generation is 404 too.
#  5. x-agentcc-prev-outcome: verify_failed on the session's next act call is
#     a kind: outcome record naming the session's previous act call, with
#     source prev_outcome.
#  6. The signals are in Redis (SCN_REDIS), every key under the gateway's
#     redis.prefix (the scenario config sets scn:<run id>:):
#     the act call's aout hash counts one verify_failed (the prev-outcome
#     repeat counted no second one), the generation's aout:last record is
#     judged and lives at most an hour, and the session's main pin is
#     mock/ok-1 with the last call's request id. No signal key is written
#     without the prefix.
#  7. On a BYOK org: an untagged call is still classified
#     on its request log entry, but the gateway keeps no Last record for it
#     (no aout:last key; feedback is 404 generation_not_found). The same call
#     agent-tagged is kept, and its feedback is 200.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> bash scripts/scenarios/28_signals.sh
set -u
SIGNALS_FIXTURES="$(cd "$(dirname "$0")" && pwd)/fixtures/signals"
SCN_MOCK_FIXTURES="$SIGNALS_FIXTURES/mock"
OTHER_ORG=org-signals-other
BYOK_ORG=org-signals-byok
source "$(dirname "$0")/lib.sh"
scn_up

# entry_for <request id>: the request's log entry, as JSON, in $SCN_DIR/entry.json.
entry_for() {
  local want=$1 deadline=$(( $(date +%s) + 30 ))
  while :; do
    curl -sS "$SCN_MOCK/__logs" | jq --arg id "$want" '[.logs[] | select(.request_id == $id)] | first // empty' >"$SCN_DIR/entry.json"
    [ -s "$SCN_DIR/entry.json" ] && return 0
    [ "$(date +%s)" -ge "$deadline" ] && scn_fail "entry $want" "no request log entry for $want"
    sleep 0.5
  done
}

# outcome_records <generation id> <source>: the kind: outcome records the
# gateway delivered for the generation from source, as a JSON list, in
# $SCN_DIR/outcomes.json, waiting up to 30 s for the first.
outcome_records() {
  local gen=$1 source=$2 deadline=$(( $(date +%s) + 30 ))
  while :; do
    curl -sS "$SCN_MOCK/__logs" |
      jq --arg g "$gen" --arg s "$source" '[.logs[] | select(.kind == "outcome" and .generation_id == $g and .source == $s)]' >"$SCN_DIR/outcomes.json"
    [ "$(jq length <"$SCN_DIR/outcomes.json")" -ge 1 ] && return 0
    [ "$(date +%s)" -ge "$deadline" ] && scn_fail "outcome $gen $source" "no $source outcome record for $gen: $(curl -sS "$SCN_MOCK/__logs" | head -c 2000)"
    sleep 0.5
  done
}

# redis_cmd <args...>: one Redis command on the scenario's DB, printing the
# reply (a bulk string, an integer, or a hash as "field=value" lines).
redis_cmd() {
  python3 -I - "$SCN_REDIS" "$SCN_REDIS_DB" "$@" <<'PY'
import socket, sys
host, port = sys.argv[1].rsplit(":", 1)
sock = socket.create_connection((host, int(port)), timeout=5)
rd = sock.makefile("rb")
def reply():
    line = rd.readline()
    kind, rest = line[:1], line[1:-2]
    if kind == b"-":
        raise SystemExit("redis: " + rest.decode())
    if kind in (b"+", b":"):
        return rest.decode()
    if kind == b"$":
        n = int(rest)
        return None if n < 0 else rd.read(n + 2)[:-2].decode()
    return [reply() for _ in range(int(rest))]
def cmd(*args):
    out = b"*%d\r\n" % len(args)
    for a in args:
        a = a.encode()
        out += b"$%d\r\n%s\r\n" % (len(a), a)
    sock.sendall(out)
    return reply()
cmd("SELECT", sys.argv[2])
r = cmd(*sys.argv[3:])
if isinstance(r, list):
    print("\n".join("%s=%s" % (r[i], r[i + 1]) for i in range(0, len(r), 2)))
elif r is not None:
    print(r)
PY
}

catalog_field() { # <model> <field>: a field of the model in GET /-/catalog
  jq -r --arg m "$1" --arg f "$2" '[.models[] | select(.id == $m)] | first | .[$f] // ""' <"$SCN_DIR/last.body"
}

scn_org_config "$SCN_ORG" '{"managed_models":{"enabled":true}}'
scn_grant "$SCN_ORG" 5

FAILING='{"model":"mock/fail-500","messages":[{"role":"user","content":"hi"}]}'
OK='{"model":"mock/ok-1","messages":[{"role":"user","content":"hi"}]}'
AGENT=(-H 'x-agentcc-agent-id: researcher' -H 'x-agentcc-run-id: R1' -H 'x-agentcc-session-id: S1' -H 'x-agentcc-step: act')

scn_check "1. 25 failed calls to the managed mock/fail-500 mark it degraded in the catalog"
scn_assert_eq 1.catalog_before 200 "$(scn_admin GET /-/catalog)"
scn_assert_eq 1.state_before active "$(catalog_field mock/fail-500 state)"
failed=0
for _ in $(seq 25); do
  code=$(scn_chat "$FAILING")
  case "$code" in 5??) failed=$((failed + 1)) ;; esac
done
scn_assert_eq 1.failed_calls 25 "$failed"
scn_assert_eq 1.upstream_calls 25 "$(scn_received 0 /chat/completions | jq '[.data[] | select(.body.model == "mock/fail-500")] | length')"
scn_assert_eq 1.catalog_after 200 "$(scn_admin GET /-/catalog)"
scn_assert_eq 1.state_after degraded "$(catalog_field mock/fail-500 state)"
scn_assert_eq 1.state_reason low_success_rate "$(catalog_field mock/fail-500 state_reason)"
scn_assert_eq 1.healthy_model active "$(catalog_field mock/ok-1 state)"

scn_check "2. an agent call is classified ok on its request log entry"
scn_assert_eq 2.status 200 "$(scn_chat "$OK" "${AGENT[@]}")"
GEN=$(scn_hdr x-agentcc-request-id)
[ -n "$GEN" ] || scn_fail 2.generation "no x-agentcc-request-id on the response"
entry_for "$GEN"
scn_assert_jq 2.outcome_class .metadata.outcome_class ok "$SCN_DIR/entry.json"

scn_check "3. feedback verify_failed for the call is one kind: outcome record in the next batch"
# Bodies are built outside "$(...)": bash 3.2 brace-expands {a,b} inside it.
VERIFY_FAILED=$(jq -cn --arg g "$GEN" '{generation_id: $g, outcome: "verify_failed", score: 0.2}')
scn_assert_eq 3.status 200 "$(scn_post /v1/feedback "$VERIFY_FAILED")"
scn_assert_jq 3.response_generation .data.generation_id "$GEN"
outcome_records "$GEN" feedback
scn_assert_jq 3.records length 1 "$SCN_DIR/outcomes.json"
scn_assert_jq 3.outcome '.[0].outcome' verify_failed "$SCN_DIR/outcomes.json"
scn_assert_jq 3.org '.[0].org_id' "$SCN_ORG" "$SCN_DIR/outcomes.json"
scn_assert_jq 3.agent '.[0].agent_id' researcher "$SCN_DIR/outcomes.json"
scn_assert_jq 3.run '.[0].agent_run_id' R1 "$SCN_DIR/outcomes.json"
scn_assert_jq 3.step '.[0].agent_step' act "$SCN_DIR/outcomes.json"
scn_assert_jq 3.model '.[0].model' mock/ok-1 "$SCN_DIR/outcomes.json"
scn_assert_jq 3.score '.[0].score' 0.2 "$SCN_DIR/outcomes.json"
scn_assert_jq 3.event_id '.[0].event_id | test("^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")' true "$SCN_DIR/outcomes.json"
scn_assert_jq 3.no_content '.[0] | keys - ["kind","event_id","org_id","generation_id","outcome","source","score","agent_run_id","agent_id","agent_step","model","ts_ms"] | length' 0 "$SCN_DIR/outcomes.json"

scn_check "4. the generation is invisible to another org's key, and an unknown one is 404"
OTHER_KEY=$(scn_key "$OTHER_ORG")
BAD=$(jq -cn --arg g "$GEN" '{generation_id: $g, outcome: "bad"}')
code=$(SCN_KEY=$OTHER_KEY scn_post /v1/feedback "$BAD")
scn_assert_eq 4.other_org_status 404 "$code"
scn_assert_jq 4.other_org_code .error.code generation_not_found
UNKNOWN='{"generation_id":"req-unknown","outcome":"ok"}'
scn_assert_eq 4.unknown_status 404 "$(scn_post /v1/feedback "$UNKNOWN")"

scn_check "5. prev-outcome verify_failed on the next act call is attributed to the previous act call"
scn_assert_eq 5.status 200 "$(scn_chat "$OK" "${AGENT[@]}" -H 'x-agentcc-prev-outcome: verify_failed')"
NEXT=$(scn_hdr x-agentcc-request-id)
outcome_records "$GEN" prev_outcome
scn_assert_jq 5.records length 1 "$SCN_DIR/outcomes.json"
scn_assert_jq 5.outcome '.[0].outcome' verify_failed "$SCN_DIR/outcomes.json"
scn_assert_jq 5.not_the_current_call "[.[] | select(.generation_id == \"$NEXT\")] | length" 0 "$SCN_DIR/outcomes.json"

scn_check "6. the signals are kept in Redis under the contract's keys, behind the gateway's redis.prefix"
if [ -n "${SCN_REDIS:-}" ]; then
  P="scn:$SCN_RUN:" # the redis.prefix lib.sh gives the scenario gateway
  # Counters decay (half-life 12 h by default), so they read to two places.
  scn_assert_eq 6.verify_failed 1.00 "$(redis_cmd HGET "${P}aout:{$SCN_ORG}:researcher:act:mock/ok-1" verify_failed | awk '{printf "%.2f", $1}')"
  scn_assert_eq 6.samples 2.00 "$(redis_cmd HGET "${P}aout:{$SCN_ORG}:researcher:act:mock/ok-1" n | awk '{printf "%.2f", $1}')"
  scn_assert_eq 6.last_judged 1 "$(redis_cmd HGET "${P}aout:last:{$SCN_ORG}:$GEN" judged)"
  scn_assert_eq 6.last_model mock/ok-1 "$(redis_cmd HGET "${P}aout:last:{$SCN_ORG}:$GEN" model)"
  last_pttl=$(redis_cmd PTTL "${P}aout:last:{$SCN_ORG}:$GEN")
  scn_assert_eq 6.last_ttl_at_most_1h yes "$( [ "$last_pttl" -gt 0 ] && [ "$last_pttl" -le 3600000 ] && echo yes || echo "no ($last_pttl ms)")"
  scn_assert_eq 6.pin_model mock/ok-1 "$(redis_cmd HGET "${P}aff:{$SCN_ORG}:S1:main" model)"
  scn_assert_eq 6.pin_request "$NEXT" "$(redis_cmd HGET "${P}aff:{$SCN_ORG}:S1:main" request_id)"
  scn_assert_eq 6.prev "$NEXT" "$(redis_cmd GET "${P}aout:prev:{$SCN_ORG}:S1:main")"
  for key in "aout:{$SCN_ORG}:researcher:act:mock/ok-1" "aout:last:{$SCN_ORG}:$GEN" "aff:{$SCN_ORG}:S1:main" "aout:prev:{$SCN_ORG}:S1:main"; do
    scn_assert_eq "6.unprefixed $key" 0 "$(redis_cmd EXISTS "$key")"
  done
else
  echo "  skip  SCN_REDIS is unset: the gateway kept the signals in memory"
fi

scn_check "7. on BYOK traffic only an agent-tagged generation is kept for feedback"
BYOK_KEY=$(scn_key "$BYOK_ORG")
scn_org_config "$BYOK_ORG" '{"providers":{"openrouter":{"api_key":"scn-test-byok-signals","enabled":true,"models":["mock/ok-1"]}}}'
scn_assert_eq 7.untagged.status 200 "$(SCN_KEY=$BYOK_KEY scn_chat "$OK")"
scn_assert_eq 7.untagged.billing_mode byok "$(scn_hdr x-agentcc-billing-mode)"
UNTAGGED=$(scn_hdr x-agentcc-request-id)
[ -n "$UNTAGGED" ] || scn_fail 7.untagged.generation "no x-agentcc-request-id on the response"
entry_for "$UNTAGGED"
scn_assert_jq 7.untagged.outcome_class .metadata.outcome_class ok "$SCN_DIR/entry.json"
UNTAGGED_OK=$(jq -cn --arg g "$UNTAGGED" '{generation_id: $g, outcome: "ok"}')
scn_assert_eq 7.untagged.feedback_status 404 "$(SCN_KEY=$BYOK_KEY scn_post /v1/feedback "$UNTAGGED_OK")"
scn_assert_jq 7.untagged.feedback_code .error.code generation_not_found
scn_assert_eq 7.tagged.status 200 "$(SCN_KEY=$BYOK_KEY scn_chat "$OK" "${AGENT[@]}")"
scn_assert_eq 7.tagged.billing_mode byok "$(scn_hdr x-agentcc-billing-mode)"
TAGGED=$(scn_hdr x-agentcc-request-id)
[ -n "$TAGGED" ] || scn_fail 7.tagged.generation "no x-agentcc-request-id on the response"
TAGGED_OK=$(jq -cn --arg g "$TAGGED" '{generation_id: $g, outcome: "ok"}')
scn_assert_eq 7.tagged.feedback_status 200 "$(SCN_KEY=$BYOK_KEY scn_post /v1/feedback "$TAGGED_OK")"
if [ -n "${SCN_REDIS:-}" ]; then
  scn_assert_eq 7.untagged.no_last_key 0 "$(redis_cmd EXISTS "${P}aout:last:{$BYOK_ORG}:$UNTAGGED")"
  scn_assert_eq 7.tagged.last_key 1 "$(redis_cmd EXISTS "${P}aout:last:{$BYOK_ORG}:$TAGGED")"
fi
scn_pass
