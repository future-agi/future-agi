#!/usr/bin/env bash
# The agent call contract on the real binary, against the mock upstream.
#  1. An Omega call (x-omega-run-id, x-omega-step: act, tools, x-agentcc-tags,
#     HTTP-Referer, metadata.omega) reaches the upstream with no agent header
#     and no metadata.omega, and logs its agent context, tags and referer.
#  2. model=omega-default against the managed mock pool is answered by a real
#     model, and the log names the alias.
#  3. Framework alias headers alone (Mastra, Claude Code, A2A) log their
#     framework and the mapped session, user and task ids, and none of them
#     reaches the upstream.
#  4. Plain caller input stays accepted: a string metadata.agent is the
#     caller's own metadata (logged, sent upstream unchanged, no agent
#     context), and an x-agentcc-trace-id that is no valid id is ignored
#     beside x-omega-trace-id.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> bash scripts/scenarios/12_agent_context.sh
set -u
source "$(dirname "$0")/lib.sh"

TOOLS='[{"type":"function","function":{"name":"search","parameters":{"type":"object","properties":{}}}}]'
CATALOG='["anthropic/claude-sonnet-5.5","openai/gpt-6-sol","qwen/qwen3.7-flash"]'

# chat_body <model> [extra json fields]: a one-message chat body.
chat_body() { echo "{\"model\":\"$1\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]${2:+,$2}}"; }

# log_for <request id>: wait (20 s) for the request's log entry at the mock,
# keep it in $SCN_DIR/log.json and print "found" or "missing".
log_for() {
  local deadline=$(( $(date +%s) + 20 ))
  while :; do
    curl -sS "$SCN_MOCK/__logs" | jq --arg id "$1" '[.logs[] | select(.request_id == $id)] | first' >"$SCN_DIR/log.json"
    if [ "$(jq -r 'type' <"$SCN_DIR/log.json")" = object ]; then echo found; return; fi
    if [ "$(date +%s)" -ge "$deadline" ]; then echo missing; return; fi
    sleep 0.5
  done
}

# last_received: the last chat request the mock was sent, in $SCN_DIR/received.json.
last_received() { scn_received 1 /chat/completions | jq '.data[0]' >"$SCN_DIR/received.json"; }

# agent_headers_received: the agent, alias and attribution header names in
# the last received request (empty when none reached the upstream).
agent_headers_received() {
  jq -r '[.headers | keys[] | ascii_downcase
    | select(test("^x-(omega|agentcc)-|^x-thread-id$|^x-resource-id$|^x-claude-code-session-id$|^x-a2a-|^http-referer$|^x-title$"))]
    | join(",")' <"$SCN_DIR/received.json"
}

scn_up
scn_org_config "$SCN_ORG" '{"managed_models":{"enabled":true}}'
scn_grant "$SCN_ORG" 5

scn_check "1. an Omega call's agent context is logged and never reaches the upstream"
BODY=$(chat_body anthropic/claude-sonnet-5.5 "\"tools\":$TOOLS,\"metadata\":{\"team\":\"core\",\"omega\":{\"workflow\":\"wf-1\"}}")
code=$(scn_chat "$BODY" -H 'x-omega-run-id: run-s1' -H 'x-omega-step: act' -H 'x-agentcc-agent-id: agent-s1' \
  -H 'x-agentcc-tags: a,b' -H 'HTTP-Referer: https://app.example/agents')
scn_assert_eq 1.status 200 "$code"
REQ=$(scn_hdr x-agentcc-request-id)
last_received
scn_assert_eq 1.no_agent_headers_upstream "" "$(agent_headers_received)"
scn_assert_jq 1.no_metadata_omega_upstream '.body.metadata | has("omega")' false "$SCN_DIR/received.json"
scn_assert_jq 1.caller_metadata_kept '.body.metadata.team' core "$SCN_DIR/received.json"
scn_assert_eq 1.logged found "$(log_for "$REQ")"
scn_assert_jq 1.run_id .agent.run_id run-s1 "$SCN_DIR/log.json"
scn_assert_jq 1.step .agent.step act "$SCN_DIR/log.json"
scn_assert_jq 1.step_source .agent.step_source header "$SCN_DIR/log.json"
scn_assert_jq 1.framework .agent.framework omega "$SCN_DIR/log.json"
scn_assert_jq 1.agent_id .agent.agent_id agent-s1 "$SCN_DIR/log.json"
scn_assert_jq 1.workflow .agent.workflow wf-1 "$SCN_DIR/log.json"
scn_assert_jq 1.identity_source .agent.identity_source header "$SCN_DIR/log.json"
scn_assert_jq 1.tags '.tags | join(",")' a,b "$SCN_DIR/log.json"
scn_assert_jq 1.referer .app.referer https://app.example/agents "$SCN_DIR/log.json"
scn_assert_jq 1.agent_tagged .metadata.agent_tagged true "$SCN_DIR/log.json"

scn_check "2. model omega-default is routed to a real model of the managed pool"
BODY=$(chat_body omega-default)
scn_assert_eq 2.status 200 "$(scn_chat "$BODY" -H 'x-omega-run-id: run-s2')"
REQ=$(scn_hdr x-agentcc-request-id)
MODEL=$(scn_json 'd["model"]')
scn_assert_eq 2.body_model_is_real yes "$(jq -n --arg m "$MODEL" --argjson c "$CATALOG" 'if ($c | index($m)) then "yes" else "no: \($m)" end' -r)"
last_received
scn_assert_jq 2.upstream_model .body.model "$MODEL" "$SCN_DIR/received.json"
scn_assert_eq 2.logged found "$(log_for "$REQ")"
scn_assert_jq 2.alias_requested .metadata.model_alias_requested omega-default "$SCN_DIR/log.json"
scn_assert_jq 2.logged_model .model "$MODEL" "$SCN_DIR/log.json"

scn_check "3. framework alias headers alone name the framework and map their ids"
# alias_call <name> <framework> <session> <user> <task> <headers...>
alias_call() {
  local name=$1 framework=$2 session=$3 user=$4 task=$5
  shift 5
  local body req
  body=$(chat_body anthropic/claude-sonnet-5.5)
  scn_assert_eq "$name.status" 200 "$(scn_chat "$body" "$@")"
  req=$(scn_hdr x-agentcc-request-id)
  last_received
  scn_assert_eq "$name.no_alias_headers_upstream" "" "$(agent_headers_received)"
  scn_assert_eq "$name.logged" found "$(log_for "$req")"
  scn_assert_jq "$name.framework" .agent.framework "$framework" "$SCN_DIR/log.json"
  scn_assert_jq "$name.identity_source" .agent.identity_source "alias:$framework" "$SCN_DIR/log.json"
  scn_assert_jq "$name.session" '.session_id // ""' "$session" "$SCN_DIR/log.json"
  # With no mapped user the auth plugin logs the key's owner.
  if [ -n "$user" ]; then scn_assert_jq "$name.user" '.user_id // ""' "$user" "$SCN_DIR/log.json"; fi
  scn_assert_jq "$name.task" '.agent.task_id // ""' "$task" "$SCN_DIR/log.json"
  scn_assert_jq "$name.inferred_step" .agent.step_source inferred "$SCN_DIR/log.json"
}
alias_call 3a mastra thread-1 resource-1 "" -H 'x-thread-id: thread-1' -H 'x-resource-id: resource-1'
alias_call 3b claude-code cc-session-1 "" "" -H 'x-claude-code-session-id: cc-session-1'
alias_call 3c a2a ctx-1 "" a2a-task-1 -H 'x-a2a-context-id: ctx-1' -H 'x-a2a-task-id: a2a-task-1'

scn_check "4. plain caller metadata and an invalid trace id are accepted"
BODY=$(chat_body anthropic/claude-sonnet-5.5 '"metadata":{"agent":"support-bot","team":"x"}')
scn_assert_eq 4a.status 200 "$(scn_chat "$BODY")"
REQ=$(scn_hdr x-agentcc-request-id)
last_received
scn_assert_jq 4a.metadata_agent_upstream '.body.metadata.agent' support-bot "$SCN_DIR/received.json"
scn_assert_jq 4a.metadata_team_upstream '.body.metadata.team' x "$SCN_DIR/received.json"
scn_assert_eq 4a.logged found "$(log_for "$REQ")"
scn_assert_jq 4a.metadata_agent_logged '.metadata.agent' support-bot "$SCN_DIR/log.json"
scn_assert_jq 4a.not_agent_tagged '.metadata.agent_tagged // ""' "" "$SCN_DIR/log.json"
scn_assert_jq 4a.no_identity '.agent.identity_source // ""' "" "$SCN_DIR/log.json"
BODY=$(chat_body anthropic/claude-sonnet-5.5)
scn_assert_eq 4b.status 200 "$(scn_chat "$BODY" -H 'x-agentcc-trace-id: my trace with spaces' -H 'x-omega-trace-id: trace-s4')"
REQ=$(scn_hdr x-agentcc-request-id)
scn_assert_eq 4b.logged found "$(log_for "$REQ")"
scn_assert_jq 4b.trace_id .agent.trace_id trace-s4 "$SCN_DIR/log.json"

scn_pass
