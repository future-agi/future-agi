#!/usr/bin/env bash
# The gateway owns the routing fields, against the real binary and the mock
# upstream:
#  1. A managed request with plugins [{id: web}] is refused with 400
#     unsupported_routing_field and never reaches the upstream.
#  2. A managed request with models[] is refused with 400
#     routing_field_not_yet_supported and never reaches the upstream.
#  3. A plain managed request reaches the upstream with the gateway-built
#     provider object (allow_fallbacks false), usage.include true, the hashed
#     org id as user (^fa_org_[0-9a-f]{24}$) and no models, plugins or preset.
#  4. (a) A managed request with web_search_options, or with a provider-run
#     server tool in tools (web_search_20250305), is refused with 400
#     unsupported_field_for_managed and never reaches the upstream.
#     (b) A managed request with extra_body {"reasoning_effort": "low"}
#     reaches the upstream with top-level reasoning_effort and no extra_body.
#  5. A BYOK OpenRouter request with plugins reaches the upstream with the
#     plugins, on the org's own key.
# The order is 2, 3, 4b, 5, then 1 and 4a: scn_fail stops at the first
# failure, and 1 and 4a run the post-routing check in ChatCompletion.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> bash scripts/scenarios/13_routing_fields.sh
set -u
source "$(dirname "$0")/lib.sh"
scn_up

ORG=$SCN_ORG
MODEL=anthropic/claude-sonnet-5.5
BYOK_KEY=scn-test-byok-openrouter # a fake org key for the mock
MANAGED_KEY=scn-test-managed-upstream

# chat <extra json fields>: a chat body for $MODEL with the fields added.
chat() {
  python3 -I -c 'import json, sys
b = {"model": sys.argv[1], "messages": [{"role": "user", "content": "hi"}]}
b.update(json.loads(sys.argv[2] or "{}"))
print(json.dumps(b))' "$MODEL" "${1:-}"
}
upstream_chats() { scn_received 0 /chat/completions | jq '.data | length'; }
# last_sent <jq filter>: the filter applied to the body of the last chat the mock received.
last_sent() { scn_received 1 /chat/completions | jq -c ".data[0].body | $1"; }
last_auth() { scn_received 1 /chat/completions | jq -c '.data[0].headers.authorization'; }

scn_org_config "$ORG" '{"managed_models":{"enabled":true}}'
scn_grant "$ORG" 5

scn_check "2. managed models[] is refused"
before=$(upstream_chats)
scn_assert_eq 2.status 400 "$(scn_chat "$(chat '{"models":["openai/gpt-6-sol"]}')")"
scn_assert_jq 2.code .error.code routing_field_not_yet_supported
scn_assert_jq 2.param .error.param models
scn_assert_eq 2.upstream_not_called "$before" "$(upstream_chats)"

scn_check "3. a plain managed request carries the gateway-built OpenRouter routing"
scn_assert_eq 3.status 200 "$(scn_chat "$(chat '{"user":"alice@example.com"}')")"
scn_assert_eq 3.billing_mode managed "$(scn_hdr x-agentcc-billing-mode)"
scn_assert_eq 3.allow_fallbacks false "$(last_sent .provider.allow_fallbacks)"
scn_assert_eq 3.data_collection '"deny"' "$(last_sent .provider.data_collection)"
scn_assert_eq 3.usage_include true "$(last_sent .usage.include)"
scn_assert_eq 3.hashed_user true "$(last_sent '.user | test("^fa_org_[0-9a-f]{24}$")')"
scn_assert_eq 3.no_routing_keys '[]' "$(last_sent '[keys[] | select(. == "models" or . == "plugins" or . == "preset" or . == "extra_body")]')"
scn_assert_eq 3.managed_key "\"Bearer $MANAGED_KEY\"" "$(last_auth)"

scn_check "4b. a managed extra_body is hoisted to the top level"
scn_assert_eq 4b.status 200 "$(scn_chat "$(chat '{"extra_body":{"reasoning_effort":"low"}}')")"
scn_assert_eq 4b.reasoning_effort '"low"' "$(last_sent .reasoning_effort)"
scn_assert_eq 4b.no_extra_body false "$(last_sent 'has("extra_body")')"

scn_check "5. a BYOK OpenRouter request keeps its plugins"
scn_org_config "$ORG" "{\"providers\":{\"openrouter\":{\"api_key\":\"$BYOK_KEY\",\"enabled\":true,\"models\":[\"$MODEL\"]}}}"
scn_assert_eq 5.status 200 "$(scn_chat "$(chat '{"plugins":[{"id":"web"}]}')")"
scn_assert_eq 5.billing_mode byok "$(scn_hdr x-agentcc-billing-mode)"
scn_assert_eq 5.byok_key "\"Bearer $BYOK_KEY\"" "$(last_auth)"
scn_assert_eq 5.plugins '[{"id":"web"}]' "$(last_sent .plugins)"
scn_assert_eq 5.no_gateway_pin null "$(last_sent .provider)"

scn_check "1. managed plugins are refused"
scn_org_config "$ORG" '{"managed_models":{"enabled":true}}'
before=$(upstream_chats)
scn_assert_eq 1.status 400 "$(scn_chat "$(chat '{"plugins":[{"id":"web"}]}')")"
scn_assert_jq 1.code .error.code unsupported_routing_field
scn_assert_jq 1.param .error.param plugins
scn_assert_eq 1.upstream_not_called "$before" "$(upstream_chats)"

scn_check "4a. managed parameters outside the allowlist are refused"
scn_assert_eq 4a.status 400 "$(scn_chat "$(chat '{"web_search_options":{}}')")"
scn_assert_jq 4a.code .error.code unsupported_field_for_managed
scn_assert_jq 4a.param .error.param web_search_options
scn_assert_eq 4a.server_tool.status 400 "$(scn_chat "$(chat '{"tools":[{"type":"web_search_20250305","name":"web_search","max_uses":50}]}')")"
scn_assert_jq 4a.server_tool.code .error.code unsupported_field_for_managed
scn_assert_jq 4a.server_tool.param .error.param 'tools[0].type'
scn_assert_eq 4a.upstream_not_called "$before" "$(upstream_chats)"

scn_pass
