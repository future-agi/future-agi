#!/usr/bin/env bash
# Embeddings on the real binary. The mock upstream (agentcc-mockup) serves
# neither Cohere's /v2/embed nor Gemini's :embedContent, so the Cohere and
# Gemini providers point at a stdlib stand-in (fixtures/embeddings/embed_standin.py)
# that answers both shapes (Gemini's with no usage) and records what it
# received; the request log goes to the mock upstream's /__logs
# (fixtures/embeddings/gateway.yaml).
#  1. Attribution: an embedding call with the typed agent headers
#     (x-agentcc-agent-id, x-agentcc-run-id, x-agentcc-step) and a caller
#     dimension in x-agentcc-metadata logs the agent, run and step and the
#     dimension; a run_id planted in x-agentcc-metadata is not logged (the
#     agent dimensions come only from the typed agent context). The response
#     names its vector space.
#  2. An input_type: query call reaches the Cohere fake as search_query.
#  3. With the target down (a model served by two Cohere hosts, with
#     failover, retries and a model fallback configured), the call fails
#     with the target's error after one upstream call, and no other host or
#     model is called: (a) on the operator's hosts, (b) on the org's own,
#     where the failure is logged with its attribution and one attempt.
#  4. A Gemini call whose upstream reports no usage logs a non-zero token
#     estimate, marked usage_estimated, and a non-zero BYOK cost.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> bash scripts/scenarios/10_embeddings.sh
#
# set -u only, like the other scenarios: lib.sh's teardown kills processes
# that may already be gone. Every step that can fail checks for it.
set -u
source "$(dirname "$0")/lib.sh"

FIX=$SCN_ROOT/fixtures/embeddings
INTERNAL_KEY=scn-test-key-internal

# The stand-in and the rendered config live in the run's scratch dir (scn_up
# empties SCN_DIR). The stand-in exits by itself when this shell does.
WORK=$SCN_HOME/scn-embeddings
rm -rf "$WORK"
mkdir -p "$WORK" || scn_fail setup "cannot create $WORK"
EMBED_PORT=$(_scn_free_port)
python3 -I "$FIX/embed_standin.py" "$EMBED_PORT" "$WORK" $$ >"$WORK/embed-standin.log" 2>&1 &
EMBED_PID=$!
deadline=$(( $(date +%s) + 10 ))
until curl -sf -o /dev/null "http://127.0.0.1:$EMBED_PORT/__health"; do
  kill -0 "$EMBED_PID" 2>/dev/null || scn_fail setup "the stand-in exited: $(tail -5 "$WORK/embed-standin.log")"
  [ "$(date +%s)" -ge "$deadline" ] && scn_fail setup "the stand-in is not answering on port $EMBED_PORT"
  sleep 0.2
done
touch "$WORK/received.jsonl"
sed "s#@@EMBED_URL@@#http://127.0.0.1:$EMBED_PORT#" "$FIX/gateway.yaml" >"$WORK/gateway.yaml" || scn_fail setup "cannot write the config"

export SCN_CONFIG_TEMPLATE=$WORK/gateway.yaml
export SCN_CATALOG_WAIT_SKIP=all # no managed provider in this config
scn_up
trap 'kill "$EMBED_PID" 2>/dev/null; scn_down' EXIT

scn_org_config "$SCN_ORG" '{"providers":{
  "cohere-a":{"api_key":"scn-test-byok-cohere","models":["embed-english-v3.0"],"enabled":true},
  "gemini":{"api_key":"scn-test-byok-gemini","models":["gemini-embedding-001"],"enabled":true}}}'

# received_count <jq filter>: how many stand-in requests match the filter.
received_count() { jq -s "[.[] | select($1)] | length" <"$WORK/received.jsonl"; }

# log_entry <request id> <out file>: wait for the request's log entry.
log_entry() {
  local deadline=$(( $(date +%s) + 30 ))
  while :; do
    curl -sS "$SCN_MOCK/__logs" | jq --arg id "$1" '[.logs[] | select(.request_id == $id)] | first // empty' >"$2"
    [ -s "$2" ] && return 0
    [ "$(date +%s)" -ge "$deadline" ] && scn_fail log_entry "no request log entry for $1"
    sleep 0.5
  done
}

scn_check "1. agent, run and step and the caller's dimension reach the log"
code=$(scn_post /v1/embeddings '{"model":"embed-english-v3.0","input":"the quarterly review moved to Friday"}' \
  -H 'x-agentcc-agent-id: memory-writer' -H 'x-agentcc-run-id: scn-embed-run-1' -H 'x-agentcc-step: memory' \
  -H 'x-agentcc-metadata: {"team":"recall","run_id":"forged"}')
scn_assert_eq 1.status 200 "$code"
scn_assert_eq 1.space "embed-english-v3.0@2:float:default" "$(scn_hdr x-agentcc-embedding-space)"
REQ1=$(scn_hdr x-agentcc-request-id)
log_entry "$REQ1" "$SCN_DIR/entry1.json"
scn_assert_jq 1.run '.agent.run_id' "scn-embed-run-1" "$SCN_DIR/entry1.json"
scn_assert_jq 1.step '.agent.step' "memory" "$SCN_DIR/entry1.json"
scn_assert_jq 1.agent '.agent.agent_id' "memory-writer" "$SCN_DIR/entry1.json"
scn_assert_jq 1.dimension '.metadata.team' "recall" "$SCN_DIR/entry1.json"
scn_assert_jq 1.no_forged_run '.metadata | has("run_id")' "false" "$SCN_DIR/entry1.json"
scn_assert_jq 1.byok '.metadata.billing_mode' "byok" "$SCN_DIR/entry1.json"
scn_assert_jq 1.one_attempt '.attempts | length' "1" "$SCN_DIR/entry1.json"
scn_assert_jq 1.attempt_tokens '.attempts[0].prompt_tokens' "8" "$SCN_DIR/entry1.json"

scn_check "2. input_type query reaches the Cohere fake as search_query"
code=$(scn_post /v1/embeddings '{"model":"embed-english-v3.0","input":"when is the quarterly review?","input_type":"query"}')
scn_assert_eq 2.status 200 "$code"
scn_assert_eq 2.upstream_input_type "search_query" "$(tail -1 "$WORK/received.jsonl" | jq -r .input_type)"
scn_assert_eq 2.space "embed-english-v3.0@2:float:query" "$(scn_hdr x-agentcc-embedding-space)"
scn_assert_eq 2.first_call_default "search_document" "$(head -1 "$WORK/received.jsonl" | jq -r .input_type)"

scn_check "3. with the target down, the call fails and no other host or model is called"
# 3a. The operator's two Cohere hosts both serve the down model, with
# failover, retries and a model fallback configured (an internal key, which
# routing and failover apply to; its requests are not in the request log).
mock_embeds_before=$(scn_received 0 embeddings | jq 'length')
code=$(SCN_KEY=$INTERNAL_KEY scn_post /v1/embeddings '{"model":"embed-multilingual-v3.0","input":"x"}' \
  -H 'x-agentcc-run-id: scn-embed-run-down' -H 'x-agentcc-step: memory')
case "$code" in
  5??) scn_assert_eq 3a.failed 5xx 5xx ;;
  *) scn_fail 3a.failed "expected the target's 5xx, got $code: $(cat "$SCN_DIR/last.body")" ;;
esac
scn_assert_eq 3a.upstream_calls 1 "$(received_count '.model == "embed-multilingual-v3.0"')"
scn_assert_eq 3a.no_fallback_model 0 "$(( $(scn_received 0 embeddings | jq 'length') - mock_embeds_before ))"
scn_assert_eq 3a.no_fallback_header "" "$(scn_hdr x-agentcc-fallback-used)"

# 3b. The org's own key, with both of the org's Cohere hosts serving the
# down model: the failure is logged with its attribution and one attempt.
scn_org_config "$SCN_ORG" '{"providers":{
  "cohere-a":{"api_key":"scn-test-byok-cohere","models":["embed-english-v3.0","embed-multilingual-v3.0"],"enabled":true},
  "cohere-b":{"api_key":"scn-test-byok-cohere","models":["embed-multilingual-v3.0"],"enabled":true},
  "gemini":{"api_key":"scn-test-byok-gemini","models":["gemini-embedding-001"],"enabled":true}}}'
code=$(scn_post /v1/embeddings '{"model":"embed-multilingual-v3.0","input":"x"}' \
  -H 'x-agentcc-run-id: scn-embed-run-down' -H 'x-agentcc-step: memory')
case "$code" in
  5??) scn_assert_eq 3b.failed 5xx 5xx ;;
  *) scn_fail 3b.failed "expected the target's 5xx, got $code: $(cat "$SCN_DIR/last.body")" ;;
esac
REQ3=$(scn_hdr x-agentcc-request-id)
scn_assert_eq 3b.upstream_calls 2 "$(received_count '.model == "embed-multilingual-v3.0"')"
scn_assert_eq 3b.no_fallback_model 0 "$(( $(scn_received 0 embeddings | jq 'length') - mock_embeds_before ))"
log_entry "$REQ3" "$SCN_DIR/entry3.json"
scn_assert_jq 3b.logged_failure '.status_code >= 500' "true" "$SCN_DIR/entry3.json"
scn_assert_jq 3b.one_attempt '.attempts | length' "1" "$SCN_DIR/entry3.json"
scn_assert_jq 3b.attempt_failed '.attempts[0].outcome' "error" "$SCN_DIR/entry3.json"
scn_assert_jq 3b.run '.agent.run_id' "scn-embed-run-down" "$SCN_DIR/entry3.json"
scn_assert_jq 3b.step '.agent.step' "memory" "$SCN_DIR/entry3.json"
scn_assert_jq 3b.no_fallback '.fallback_used' "false" "$SCN_DIR/entry3.json"

scn_check "4. a Gemini call with no upstream usage logs a non-zero estimate"
code=$(scn_post /v1/embeddings '{"model":"gemini-embedding-001","input":"a passage the org indexes with its own Gemini key","input_type":"document"}')
scn_assert_eq 4.status 200 "$code"
scn_assert_eq 4.task_type "RETRIEVAL_DOCUMENT" "$(tail -1 "$WORK/received.jsonl" | jq -r .task_type)"
scn_assert_eq 4.space "gemini-embedding-001@3:float:document" "$(scn_hdr x-agentcc-embedding-space)"
scn_assert_jq 4.response_usage '.usage.prompt_tokens > 0' "true"
REQ4=$(scn_hdr x-agentcc-request-id)
log_entry "$REQ4" "$SCN_DIR/entry4.json"
scn_assert_jq 4.usage_estimated '.usage_estimated' "true" "$SCN_DIR/entry4.json"
scn_assert_jq 4.attempt_estimated '.attempts[0].usage_estimated' "true" "$SCN_DIR/entry4.json"
scn_assert_jq 4.attempt_tokens '.attempts[0].prompt_tokens > 0' "true" "$SCN_DIR/entry4.json"
scn_assert_jq 4.cost_nonzero '.cost > 0' "true" "$SCN_DIR/entry4.json"
scn_assert_jq 4.byok '.metadata.billing_mode' "byok" "$SCN_DIR/entry4.json"
scn_assert_eq 4.no_key_in_record 0 "$(grep -c 'scn-test-byok-gemini' "$WORK/received.jsonl")"

scn_pass
