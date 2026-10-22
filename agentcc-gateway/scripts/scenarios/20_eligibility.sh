#!/usr/bin/env bash
# One eligibility engine on every routing path, against the real binary
# and the mock upstream (fixtures/eligibility/mock: a catalog of five managed models;
# claude-sonnet-5.5 has three OpenRouter endpoints, of which anthropic and
# google-vertex are on the ZDR list and deepinfra is not).
#  1. A key restricted to allowed_models [openai/gpt-4o-mini] calling
#     model=auto gets 200, and x-agentcc-auto-model is in the key's set.
#  2. With managed_models.deny_vendors [x-ai], an explicit x-ai/grok-5 is 403
#     model_not_permitted with metadata.reason managed_model_not_allowed,
#     and never reaches the upstream.
#  3. With the org's data_policy.zdr true, /__received shows provider.zdr
#     true and an order and only that name only the fixture's ZDR endpoints.
#  4. A plain managed request carries provider.data_collection deny; with
#     managed_models.allow_data_collection it carries allow.
#  5. A transcript with earlier non-Gemini tool calls sent to model=auto
#     (cost preference, where google/gemini-3-pro is the cheapest) never
#     selects a google/gemini-3* model, and the decision in the request log
#     lists tool_history_incompatible.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> bash scripts/scenarios/20_eligibility.sh
set -u
SCN_MOCK_FIXTURES="$(cd "$(dirname "$0")" && pwd)/fixtures/eligibility/mock"
source "$(dirname "$0")/lib.sh"
scn_up

ORG=$SCN_ORG
CLAUDE=anthropic/claude-sonnet-5.5

# chat <model> [extra json fields]: a one-message chat body.
chat() {
  python3 -I -c 'import json, sys
b = {"model": sys.argv[1], "messages": [{"role": "user", "content": "hi"}]}
b.update(json.loads(sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else "{}"))
print(json.dumps(b))' "$@"
}
upstream_chats() { scn_received 0 /chat/completions | jq '.data | length'; }
# last_sent <jq filter>: the filter applied to the body of the last chat the mock received.
last_sent() { scn_received 1 /chat/completions | jq -c ".data[0].body | $1"; }

scn_org_config "$ORG" '{"managed_models":{"enabled":true}}'
scn_grant "$ORG" 5

# The endpoints are read lazily: the admin detail lookup loads claude's
# from the mock, with the ZDR list applied.
code=$(scn_admin GET "/-/catalog/endpoints?model=$CLAUDE&provider=openrouter")
scn_assert_eq setup.endpoints_status 200 "$code"
scn_assert_eq setup.endpoints_loaded 3 "$(jq '.data.endpoints | length' <"$SCN_DIR/last.body")"

scn_check "1. auto stays inside the key's allowed_models"
RESTRICTED=$(scn_key "$ORG" '{"models":["openai/gpt-4o-mini"]}')
code=$(SCN_KEY=$RESTRICTED scn_chat "$(chat auto)" -H 'x-agentcc-auto-preference: cost')
scn_assert_eq 1.status 200 "$code"
scn_assert_eq 1.auto_model openai/gpt-4o-mini "$(scn_hdr x-agentcc-auto-model)"
scn_assert_eq 1.upstream_model '"openai/gpt-4o-mini"' "$(last_sent .model)"

scn_check "2. managed deny_vendors refuses an explicit model before any upstream call"
scn_org_config "$ORG" '{"managed_models":{"enabled":true,"deny_vendors":["x-ai"]}}'
before=$(upstream_chats)
scn_assert_eq 2.status 403 "$(scn_chat "$(chat x-ai/grok-5)")"
scn_assert_jq 2.code .error.code model_not_permitted
scn_assert_jq 2.reason .error.metadata.reason managed_model_not_allowed
scn_assert_eq 2.upstream_not_called "$before" "$(upstream_chats)"

scn_check "3. org ZDR pins the request to the ZDR endpoints"
scn_org_config "$ORG" '{"managed_models":{"enabled":true},"routing":{"data_policy":{"zdr":true}}}'
scn_assert_eq 3.status 200 "$(scn_chat "$(chat "$CLAUDE")")"
scn_assert_eq 3.zdr true "$(last_sent .provider.zdr)"
scn_assert_eq 3.order_is_only true "$(last_sent '.provider.order == .provider.only')"
scn_assert_eq 3.only_zdr_endpoints '["anthropic","google-vertex"]' "$(last_sent '.provider.only | sort')"

scn_check "4. managed attempts deny data collection unless the org opts in"
scn_org_config "$ORG" '{"managed_models":{"enabled":true}}'
scn_assert_eq 4.status 200 "$(scn_chat "$(chat "$CLAUDE")")"
scn_assert_eq 4.deny '"deny"' "$(last_sent .provider.data_collection)"
scn_org_config "$ORG" '{"managed_models":{"enabled":true,"allow_data_collection":true}}'
scn_assert_eq 4.optin.status 200 "$(scn_chat "$(chat "$CLAUDE")")"
scn_assert_eq 4.optin.allow '"allow"' "$(last_sent .provider.data_collection)"

scn_check "5. unsigned tool history never goes to a Gemini 3 model"
scn_org_config "$ORG" '{"managed_models":{"enabled":true}}'
transcript=$(python3 -I -c 'import json
print(json.dumps({"model": "auto", "messages": [
    {"role": "user", "content": "weather in Paris?"},
    {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "weather", "arguments": "{\"city\":\"Paris\"}"}}]},
    {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
    {"role": "user", "content": "and tomorrow?"}]}))')
code=$(scn_chat "$transcript" -H 'x-agentcc-auto-preference: cost')
scn_assert_eq 5.status 200 "$code"
chosen=$(scn_hdr x-agentcc-auto-model)
case "$chosen" in
  google/gemini-3*) scn_fail 5.not_gemini "auto chose $chosen for an unsigned tool history" ;;
esac
scn_assert_eq 5.chosen_not_gemini true "$([ -n "$chosen" ] && echo true || echo false)"
request_id=$(scn_hdr x-agentcc-request-id)
deadline=$(( $(date +%s) + 30 ))
while :; do
  curl -sS "$SCN_MOCK/__logs" | jq --arg id "$request_id" '[.logs[] | select(.request_id == $id)] | first // empty' >"$SCN_DIR/entry.json"
  [ -s "$SCN_DIR/entry.json" ] && break
  [ "$(date +%s)" -ge "$deadline" ] && scn_fail 5.entry "no request log entry for $request_id"
  sleep 0.5
done
scn_assert_eq 5.rejected_gemini true "$(jq -r '.metadata.auto_decision | fromjson | [.rejected[] | select(.model == "google/gemini-3-pro" and .reason == "tool_history_incompatible")] | length > 0' <"$SCN_DIR/entry.json")"

scn_pass
