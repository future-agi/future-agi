#!/usr/bin/env bash
# The capability registry on the real binary.
#  1. GET /v1/capabilities is scoped to the key: before the org opts in to
#     managed models it lists neither the managed provider nor the operator's
#     FutureAGI-credentialed one; the key's org has no providers of its own.
#  2. After the opt-in, chat.completions is published with launch_tier=launch
#     and the managed provider is listed (managed, features read from the
#     mock's catalog, no managed_resale_terms: admin-only); the operator's
#     provider is not.
#  3. ?model= reports one catalog model's flags, context and modalities, and
#     whether managed traffic may use it (priced through Handlers.deps).
#  4. GET /-/capabilities needs the admin token, lists every provider and
#     shows managed_resale_terms (unconfirmed).
#  5. A live qualification file written after startup marks the cell
#     qualified on the next request; a mock run is ignored, with a warning.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> bash scripts/scenarios/16_capabilities.sh
set -u
source "$(dirname "$0")/lib.sh"

# The scenario config is base.yaml plus a qualification file in the run dir.
TEMPLATE=$SCN_HOME/capabilities-template.yaml
mkdir -p "$SCN_HOME"
{
  cat "$SCN_ROOT/config/base.yaml"
  printf 'capabilities:\n  qualification_file: "@@SCN_DIR@@/qualification.json"\n'
} >"$TEMPLATE"
SCN_CONFIG_TEMPLATE=$TEMPLATE
MODEL=anthropic/claude-sonnet-5.5 # in the mock's catalog: tools, structured outputs, reasoning, images

# cap_get <path> [bearer]: GET from the gateway (default bearer: $SCN_KEY);
# prints the HTTP status, keeps the body in $SCN_DIR/last.body.
cap_get() {
  curl -sS -o "$SCN_DIR/last.body" -D "$SCN_DIR/last.headers" -w '%{http_code}' \
    "$SCN_GW$1" -H "Authorization: Bearer ${2-$SCN_KEY}"
}

# CHAT selects the chat.completions row.
CHAT='.endpoints[] | select(.endpoint == "chat.completions")'

scn_up

scn_check "1. before the opt-in, the key reaches no managed and no operator provider"
scn_org_config "$SCN_ORG" '{}'
scn_assert_eq 1.status 200 "$(cap_get /v1/capabilities)"
scn_assert_jq 1.no_providers "[$CHAT | .providers | keys[]] | length" 0
scn_assert_jq 1.chat_tier_without_a_provider "$CHAT | .launch_tier" unsupported

scn_check "2. opted in: chat.completions is launch tier and the managed provider is listed"
scn_org_config "$SCN_ORG" '{"managed_models":{"enabled":true}}'
scn_assert_eq 2.status 200 "$(cap_get /v1/capabilities)"
scn_assert_jq 2.chat_launch_tier "$CHAT | .launch_tier" launch
scn_assert_jq 2.managed_listed "$CHAT | .providers | keys | join(\",\")" openrouter
scn_assert_jq 2.credential_type "$CHAT | .providers.openrouter.credential_type" managed
scn_assert_jq 2.status_supported "$CHAT | .providers.openrouter.status" supported
scn_assert_jq 2.no_resale_terms_for_keys "$CHAT | .providers.openrouter | has(\"managed_resale_terms\")" false
scn_assert_jq 2.features_from_catalog "$CHAT | .providers.openrouter.source" catalog
scn_assert_jq 2.catalog_tools "$CHAT | .providers.openrouter.features.tools" true
scn_assert_jq 2.no_audio_in_catalog "$CHAT | .providers.openrouter.features.audio_input" false
scn_assert_jq 2.managed_allowed "$CHAT | .providers.openrouter.managed_allowed" true
scn_assert_jq 2.no_evidence_not_qualified "$CHAT | .providers.openrouter.qualified" false
scn_assert_jq 2.no_evidence_null_at "$CHAT | .providers.openrouter.qualified_at" null
scn_assert_jq 2.images_not_managed '.endpoints[] | select(.endpoint == "images") | .providers.openrouter.managed_allowed' false
scn_assert_jq 2.batch_is_admin_only '.endpoints[] | select(.endpoint == "batch") | .providers.openrouter.status' unsupported
scn_assert_jq 2.endpoint_count '.endpoints | length' 22

scn_check "3. ?model= reports one model's flags"
scn_assert_eq 3.status 200 "$(cap_get "/v1/capabilities?model=$MODEL")"
scn_assert_jq 3.tools '.providers.openrouter.flags.tools' true
scn_assert_jq 3.json_schema '.providers.openrouter.flags.json_schema' true
scn_assert_jq 3.reasoning '.providers.openrouter.flags.reasoning' true
scn_assert_jq 3.vision '.providers.openrouter.flags.vision' true
scn_assert_jq 3.input_modalities '.providers.openrouter.modalities.input | join(",")' text,image
scn_assert_jq 3.chat_managed_allowed '.providers.openrouter.endpoints["chat.completions"].managed_allowed' true
scn_assert_eq 3.unknown_model 404 "$(cap_get "/v1/capabilities?model=no/such-model")"
scn_assert_jq 3.unknown_model_code '.error.code' model_not_found

scn_check "4. the admin view needs the token and lists every provider"
scn_assert_eq 4.no_token 401 "$(cap_get /-/capabilities "")"
scn_assert_eq 4.virtual_key 401 "$(cap_get /-/capabilities)"
scn_assert_eq 4.status 200 "$(scn_admin GET /-/capabilities)"
scn_assert_jq 4.all_providers "$CHAT | .providers | keys | join(\",\")" openrouter,operator-openai
scn_assert_jq 4.operator_byok "$CHAT | .providers[\"operator-openai\"].credential_type" byok
scn_assert_jq 4.resale_unconfirmed "$CHAT | .providers.openrouter.managed_resale_terms" unconfirmed
scn_assert_jq 4.batch_for_admin '.endpoints[] | select(.endpoint == "batch") | .providers["operator-openai"].status' supported

scn_check "5. a live qualification file is read on the next request; a mock run is ignored"
cat >"$SCN_DIR/qualification.json" <<'JSON'
{"mode": "mock", "ran_at": "2026-10-15T09:00:00Z",
 "results": [{"provider": "openrouter", "endpoint": "chat.completions", "feature": "stream", "status": "pass"}]}
JSON
scn_assert_eq 5.mock_status 200 "$(scn_admin GET /-/capabilities)"
scn_assert_jq 5.mock_not_qualified "$CHAT | .providers.openrouter.qualified" false
scn_assert_eq 5.mock_warned 1 "$(scn_gateway_log | grep -c 'capabilities: qualification file ignored' | awk '{print ($1 > 0)}')"
cat >"$SCN_DIR/qualification.json" <<'JSON'
{"mode": "live", "ran_at": "2026-10-15T09:00:00Z",
 "results": [{"provider": "openrouter", "endpoint": "chat.completions", "feature": "stream", "status": "pass"},
             {"provider": "openrouter", "endpoint": "embeddings", "feature": "basic", "status": "pass"}]}
JSON
scn_assert_eq 5.live_status 200 "$(scn_admin GET /-/capabilities)"
scn_assert_jq 5.qualified "$CHAT | .providers.openrouter.qualified" true
scn_assert_jq 5.qualified_at "$CHAT | .providers.openrouter.qualified_at" 2026-10-15T09:00:00Z
scn_assert_jq 5.source "$CHAT | .providers.openrouter.source" qualified
scn_assert_jq 5.live_pass_settles_provider_dependent \
  '.endpoints[] | select(.endpoint == "embeddings") | .providers.openrouter.status' supported
scn_assert_jq 5.unqualified_stays_null "$CHAT | .providers[\"operator-openai\"].qualified_at" null

scn_pass
