#!/usr/bin/env bash
# Gate check: agent headers, their aliases and the routing fields never
# reach the upstream (the mock's /__received).
#  1. A managed call carrying every kind at once: x-agentcc-* agent headers,
#     x-omega-* fields, attribution headers, body session_id, metadata.agent
#     and metadata.omega, and an extra_body. The upstream sees no agent,
#     alias or attribution header, no session_id, extra_body or metadata
#     agent context, and only the gateway-built provider object.
#  2. The same headers plus plugins, route and transforms (the routing fields
#     the gateway accepts but does not interpret) on a non-OpenRouter
#     upstream (an internal key on operator-openai): none of them arrives.
#  3. Each framework's alias headers (Mastra, Claude Code, A2A) on the same
#     upstream: none arrives.
# (A BYOK OpenRouter upstream receives the caller's plugins, route,
# transforms and provider object by contract; 13_routing_fields.sh check 5
# covers it.)
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<n> bash gates/strip_all.sh
set -u
GATES_DIR=$(cd "$(dirname "$0")" && pwd)
SCN_CONFIG_TEMPLATE=$GATES_DIR/fixtures/catalog-priced/gateway.yaml
SCN_MOCK_FIXTURES=$GATES_DIR/fixtures/catalog-priced/mock
source "$GATES_DIR/lib/gate_common.sh"
scn_up

AGENT_HEADERS=(-H 'x-agentcc-run-id: run-strip' -H 'x-agentcc-parent-run-id: run-parent' -H 'x-agentcc-agent-id: agent-strip'
  -H 'x-agentcc-step: act' -H 'x-agentcc-session-id: sess-strip' -H 'x-agentcc-tags: a,b' -H 'x-agentcc-user: user-strip'
  -H 'x-omega-task-id: task-strip' -H 'x-omega-workflow: wf-strip' -H 'x-omega-turn: 2'
  -H 'HTTP-Referer: https://app.example/agents' -H 'X-Title: Strip gate' -H 'X-OpenRouter-Title: Strip gate')
# Body keys no upstream may receive: the caller's routing fields and the
# gateway-read session id (a gateway-built provider object is checked apart).
ROUTING_KEYS='["models","fallbacks","route","plugins","transforms","preset","session_id","caching","providerOptions","extra_body"]'

last_received() { scn_received 1 /chat/completions | jq '.data[0]' >"$SCN_DIR/received.json"; }
# leaked_headers: agent, alias and attribution header names that arrived.
leaked_headers() {
  jq -r '[.headers | keys[] | ascii_downcase
    | select(test("^x-(omega|agentcc)-|^x-thread-id$|^x-resource-id$|^x-claude-code-session-id$|^x-a2a-|^x-session-id$|^http-referer$|^x-title$|^x-openrouter-title$"))]
    | join(",")' <"$SCN_DIR/received.json"
}
leaked_keys() { jq -c --argjson k "$ROUTING_KEYS" '[.body | keys[] | select(. as $x | $k | index($x))]' <"$SCN_DIR/received.json"; }
agent_metadata() { jq -c '[.body.metadata // {} | keys[] | select(. == "agent" or . == "omega")]' <"$SCN_DIR/received.json"; }

scn_check "1. managed: agent headers, aliases, session id and extra_body never arrive"
scn_org_config "$SCN_ORG" '{"managed_models":{"enabled":true}}'
scn_grant "$SCN_ORG" 5
BODY=$(jq -nc '{model: "anthropic/claude-sonnet-5.5", messages: [{role: "user", content: "hi"}], session_id: "sess-strip",
  extra_body: {reasoning_effort: "low"}, metadata: {team: "core", agent: {workflow: "wf-strip"}, omega: {env: "dev"}}}')
scn_assert_eq 1.status 200 "$(scn_chat "$BODY" "${AGENT_HEADERS[@]}")"
scn_assert_eq 1.billing_mode managed "$(scn_hdr x-agentcc-billing-mode)"
last_received
scn_assert_eq 1.no_agent_headers "" "$(leaked_headers)"
scn_assert_eq 1.no_routing_keys '[]' "$(leaked_keys)"
scn_assert_eq 1.no_agent_metadata '[]' "$(agent_metadata)"
scn_assert_jq 1.caller_metadata_kept .body.metadata.team core "$SCN_DIR/received.json"
scn_assert_jq 1.gateway_provider_object .body.provider.allow_fallbacks false "$SCN_DIR/received.json"

scn_check "2. a non-OpenRouter upstream gets no agent header and no routing field"
BODY=$(jq -nc '{model: "gpt-4o", messages: [{role: "user", content: "hi"}], session_id: "sess-strip",
  plugins: [{id: "web"}], route: "fallback", transforms: ["middle-out"], metadata: {agent: {workflow: "wf-strip"}}}')
scn_assert_eq 2.status 200 "$(SCN_KEY=$GATE_INTERNAL_KEY scn_chat "$BODY" "${AGENT_HEADERS[@]}")"
scn_assert_eq 2.provider operator-openai "$(scn_hdr x-agentcc-provider)"
last_received
scn_assert_jq 2.operator_upstream .headers.authorization "Bearer scn-test-operator-upstream" "$SCN_DIR/received.json"
scn_assert_eq 2.no_agent_headers "" "$(leaked_headers)"
scn_assert_eq 2.no_routing_keys '[]' "$(leaked_keys)"
scn_assert_jq 2.no_provider_object '.body | has("provider")' false "$SCN_DIR/received.json"
scn_assert_eq 2.no_agent_metadata '[]' "$(agent_metadata)"

scn_check "3. framework alias headers never arrive"
alias_call() { # name headers...
  local name=$1
  shift
  scn_assert_eq "3.$name.status" 200 "$(SCN_KEY=$GATE_INTERNAL_KEY scn_chat '{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}' "$@")"
  last_received
  scn_assert_eq "3.$name.no_alias_headers" "" "$(leaked_headers)"
}
alias_call mastra -H 'x-thread-id: thread-1' -H 'x-resource-id: resource-1'
alias_call claude-code -H 'x-claude-code-session-id: cc-session-1'
alias_call a2a -H 'x-a2a-context-id: ctx-1' -H 'x-a2a-task-id: a2a-task-1'
alias_call generic -H 'x-session-id: generic-1'

scn_pass
