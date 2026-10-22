#!/usr/bin/env bash
# Every upstream attempt is recorded, through the real gateway binary and
# the mock upstream (fixtures/attempts: gateway.yaml and the mock's catalog).
#  1. With the org's model_fallbacks {mock/fail-503: [mock/ok-2]}, the request
#     returns 200; the request log's attempts[0] is mock/fail-503's error
#     (upstream_5xx) and attempts[1] the model_fallback to mock/ok-2, a success.
#  2. With x-mock-behavior fail:429:n=2 and the org's retries on, the request
#     returns 200 after attempts primary, retry, retry on one provider. Only
#     internal keys fail over (and so retry), and the request log leaves them
#     out, so the attempts are read from the gateway's debug attempt log.
#  3. A stream records its time to first byte: with latency:60 on the mock,
#     the stream's one attempt has ttfb_ms of at least 60.
#  4. Cached and reasoning tokens, and the upstream OpenRouter reports as
#     having served, reach the attempt and the entry.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> bash scripts/scenarios/11_attempts.sh
set -u
ATTEMPTS_FIXTURES="$(cd "$(dirname "$0")" && pwd)/fixtures/attempts"
SCN_CONFIG_TEMPLATE="$ATTEMPTS_FIXTURES/gateway.yaml"
SCN_MOCK_FIXTURES="$ATTEMPTS_FIXTURES/mock"
INTERNAL_KEY=scn-test-key-attempts-internal
source "$(dirname "$0")/lib.sh"
scn_up

# entry_for <request id>: the request log entry the gateway delivered for the
# request, as JSON, in $SCN_DIR/entry.json.
entry_for() {
  local want=$1 deadline=$(( $(date +%s) + 30 ))
  while :; do
    curl -sS "$SCN_MOCK/__logs" | jq --arg id "$want" '[.logs[] | select(.request_id == $id)] | first // empty' >"$SCN_DIR/entry.json"
    [ -s "$SCN_DIR/entry.json" ] && return 0
    [ "$(date +%s)" -ge "$deadline" ] && scn_fail "entry $want" "no request log entry for $want: $(curl -sS "$SCN_MOCK/__logs" | head -c 2000)"
    sleep 0.5
  done
}
attempt() { jq -r ".attempts[$1].$2 // \"\"" <"$SCN_DIR/entry.json"; }

# logged_attempts <request id>: the request's attempts from the gateway's
# debug attempt log, as a JSON list, in $SCN_DIR/logged.json.
logged_attempts() {
  grep '^{' "$SCN_DIR/gateway.log" |
    jq -s --arg id "$1" '[.[] | select(.msg == "upstream attempt" and .request_id == $id)]' >"$SCN_DIR/logged.json"
}

scn_org_config "$SCN_ORG" '{"managed_models":{"enabled":true},
  "routing":{"failover":{"enabled":true},
             "retry":{"enabled":true,"max_retries":2,"initial_delay":"10ms","max_delay":"20ms"},
             "model_fallbacks":{"mock/fail-503":["mock/ok-2"]}}}'
scn_grant "$SCN_ORG" 5

scn_check "1. a failed model falls back: the failure and the fallback are both in attempts[]"
scn_assert_eq 1.status 200 "$(scn_chat '{"model":"mock/fail-503","messages":[{"role":"user","content":"hi"}]}')"
entry_for "$(scn_hdr x-agentcc-request-id)"
scn_assert_jq 1.attempt_count .attempt_count 2 "$SCN_DIR/entry.json"
scn_assert_eq 1.first_kind primary "$(attempt 0 kind)"
scn_assert_eq 1.first_model mock/fail-503 "$(attempt 0 model)"
scn_assert_eq 1.first_outcome error "$(attempt 0 outcome)"
scn_assert_eq 1.first_error_class upstream_5xx "$(attempt 0 error_class)"
scn_assert_eq 1.first_error_origin upstream "$(attempt 0 error_origin)"
scn_assert_eq 1.second_kind model_fallback "$(attempt 1 kind)"
scn_assert_eq 1.second_model mock/ok-2 "$(attempt 1 model)"
scn_assert_eq 1.second_reason status_5xx "$(attempt 1 reason)"
scn_assert_eq 1.second_outcome success "$(attempt 1 outcome)"
scn_assert_eq 1.provider openrouter "$(attempt 1 provider)"
scn_assert_eq 1.credential_type managed "$(attempt 1 credential_type)"

scn_check "2. retries on: two 429s then a success are attempts primary, retry, retry"
code=$(SCN_KEY=$INTERNAL_KEY scn_chat '{"model":"mock/ok-1","messages":[{"role":"user","content":"hi"}]}' -H 'x-mock-behavior: fail:429:n=2')
scn_assert_eq 2.status 200 "$code"
logged_attempts "$(scn_hdr x-agentcc-request-id)"
scn_assert_jq 2.kinds '[.[].kind] | join(",")' primary,retry,retry "$SCN_DIR/logged.json"
scn_assert_jq 2.outcomes '[.[].outcome] | join(",")' error,error,success "$SCN_DIR/logged.json"
scn_assert_jq 2.classes '[.[0,1].error_class] | join(",")' rate_limited,rate_limited "$SCN_DIR/logged.json"
scn_assert_jq 2.reasons '[.[1,2].reason] | join(",")' status_429,status_429 "$SCN_DIR/logged.json"
scn_assert_jq 2.one_provider '[.[].provider] | unique | length' 1 "$SCN_DIR/logged.json"
scn_assert_jq 2.ids '[.[].attempt_id | split(".") | last] | join(",")' 1,2,3 "$SCN_DIR/logged.json"
scn_assert_eq 2.upstream_calls 3 "$(scn_received 3 /chat/completions | jq '[.data[] | select(.headers.authorization == "Bearer scn-test-attempts-upstream")] | length')"

scn_check "3. a stream's attempt records its time to first byte"
scn_assert_eq 3.status 200 "$(scn_stream '{"model":"mock/ok-1","messages":[{"role":"user","content":"hi"}]}' -H 'x-mock-behavior: latency:60')"
scn_assert_eq 3.done '[DONE]' "$(scn_json 'd[-1]')"
entry_for "$(scn_hdr x-agentcc-request-id)"
scn_assert_jq 3.attempts '.attempts | length' 1 "$SCN_DIR/entry.json"
scn_assert_jq 3.ttfb_at_least_60 '.attempts[0].ttfb_ms >= 60' true "$SCN_DIR/entry.json"

scn_check "4. cached and reasoning tokens, and the served upstream, reach the attempt and the entry"
scn_assert_eq 4.status 200 "$(scn_chat '{"model":"mock/ok-1","messages":[{"role":"user","content":"hi"}]}' \
  -H 'x-mock-behavior: cached_tokens:40,reasoning_tokens:20,served:fireworks')"
entry_for "$(scn_hdr x-agentcc-request-id)"
scn_assert_eq 4.attempt_cached 40 "$(attempt 0 cached_tokens)"
scn_assert_eq 4.attempt_reasoning 20 "$(attempt 0 reasoning_tokens)"
scn_assert_eq 4.attempt_served fireworks "$(attempt 0 served_upstream_provider)"
scn_assert_jq 4.entry_cached .cached_tokens 40 "$SCN_DIR/entry.json"
scn_assert_jq 4.entry_reasoning .reasoning_tokens 20 "$SCN_DIR/entry.json"
scn_assert_jq 4.entry_served .served_upstream_provider fireworks "$SCN_DIR/entry.json"

scn_pass
