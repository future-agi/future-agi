#!/usr/bin/env bash
# Per-key content mode on the real binary, against the mock upstream
# (fixtures/content-mode/gateway.yaml: base.yaml with request bodies captured).
#  1. A key whose metadata says log_content=none logs no request_body,
#     response_body or request_headers, and its entry says content_mode=none.
#  2. Another key of the same org, with no log_content, still logs the full
#     bodies and headers (content_mode=full).
#  3. x-agentcc-log-content tightens one request of the full key to metadata,
#     and cannot loosen the none key back to full.
#  4. A key update that sets log_content (PUT /-/keys/{id}, the push Django's
#     key serializer makes) applies to that key's next request.
#  5. A warn-action guardrail's message quotes the prompt word it matched and
#     reaches the log as metadata.guardrail_warnings: the none key's entry has
#     neither the warning nor the word anywhere; the full key's keeps both.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> bash scripts/scenarios/34_content_mode.sh
set -u
SCN_CONFIG_TEMPLATE="$(cd "$(dirname "$0")" && pwd)/fixtures/content-mode/gateway.yaml"
source "$(dirname "$0")/lib.sh"

BODY='{"model":"anthropic/claude-sonnet-5.5","messages":[{"role":"user","content":"content-mode private prompt"}]}'

# entry_for <request id>: wait (30 s) for the request's log entry at the mock
# and keep it in $SCN_DIR/entry.json.
entry_for() {
  local deadline=$(( $(date +%s) + 30 ))
  while :; do
    curl -sS "$SCN_MOCK/__logs" | jq --arg id "$1" '[.logs[] | select(.request_id == $id)] | first // empty' >"$SCN_DIR/entry.json"
    [ -s "$SCN_DIR/entry.json" ] && return 0
    [ "$(date +%s)" -ge "$deadline" ] && scn_fail "entry $1" "no request log entry for $1"
    sleep 0.5
  done
}

# kept <field>: "yes" when the entry has a non-null field, else "no".
kept() { jq -r --arg f "$1" 'if .[$f] == null then "no" else "yes" end' <"$SCN_DIR/entry.json"; }

# chat_entry <label> [curl args...]: one chat that must succeed; its log
# entry lands in $SCN_DIR/entry.json.
chat_entry() {
  local label=$1 code
  shift
  code=$(scn_chat "$BODY" "$@")
  scn_assert_eq "$label.status" 200 "$code"
  entry_for "$(scn_hdr x-agentcc-request-id)"
}

scn_up
scn_org_config "$SCN_ORG" '{"managed_models":{"enabled":true}}'
scn_grant "$SCN_ORG" 5
NONE_KEY=$(scn_key "$SCN_ORG" '{"metadata":{"log_content":"none"}}')
[ -n "$NONE_KEY" ] && [ "$NONE_KEY" != null ] || scn_fail key "no key returned: $(cat "$SCN_DIR/last.body")"

scn_check "1. a key with log_content=none logs no bodies or request headers"
SCN_KEY=$NONE_KEY chat_entry 1
scn_assert_jq 1.content_mode .content_mode none "$SCN_DIR/entry.json"
scn_assert_eq 1.request_body no "$(kept request_body)"
scn_assert_eq 1.response_body no "$(kept response_body)"
scn_assert_eq 1.request_headers no "$(kept request_headers)"
scn_assert_eq 1.no_prompt_anywhere 0 "$(grep -c 'content-mode private prompt' "$SCN_DIR/entry.json")"
scn_assert_jq 1.usage_kept '.total_tokens > 0' true "$SCN_DIR/entry.json"
scn_assert_jq 1.org_kept .metadata.org_id "$SCN_ORG" "$SCN_DIR/entry.json"

scn_check "2. another key of the same org still logs full bodies"
chat_entry 2
scn_assert_jq 2.content_mode .content_mode full "$SCN_DIR/entry.json"
scn_assert_eq 2.request_body yes "$(kept request_body)"
scn_assert_eq 2.response_body yes "$(kept response_body)"
scn_assert_eq 2.request_headers yes "$(kept request_headers)"
scn_assert_jq 2.prompt_logged '.request_body.messages[0].content' "content-mode private prompt" "$SCN_DIR/entry.json"
scn_assert_jq 2.org_kept .metadata.org_id "$SCN_ORG" "$SCN_DIR/entry.json"

scn_check "3. the request header tightens a request and cannot loosen a key"
chat_entry 3a -H 'x-agentcc-log-content: metadata'
scn_assert_jq 3a.content_mode .content_mode metadata "$SCN_DIR/entry.json"
scn_assert_eq 3a.request_body no "$(kept request_body)"
scn_assert_eq 3a.request_headers no "$(kept request_headers)"
SCN_KEY=$NONE_KEY chat_entry 3b -H 'x-agentcc-log-content: full'
scn_assert_jq 3b.content_mode .content_mode none "$SCN_DIR/entry.json"
scn_assert_eq 3b.request_body no "$(kept request_body)"

scn_check "4. a key update that sets log_content applies to the key's next request"
META_KEY=$(scn_key "$SCN_ORG")
META_KEY_ID=$(jq -r .id <"$SCN_DIR/last.body")
SCN_KEY=$META_KEY chat_entry 4a
scn_assert_jq 4a.content_mode_before .content_mode full "$SCN_DIR/entry.json"
code=$(scn_admin PUT "/-/keys/$META_KEY_ID" "{\"metadata\":{\"org_id\":\"$SCN_ORG\",\"log_content\":\"metadata\"}}")
scn_assert_eq 4.update_status 200 "$code"
SCN_KEY=$META_KEY chat_entry 4b
scn_assert_jq 4b.content_mode_after .content_mode metadata "$SCN_DIR/entry.json"
scn_assert_eq 4b.request_body no "$(kept request_body)"

scn_check "5. none drops guardrail warnings, which quote the prompt"
FLAGGED_BODY='{"model":"anthropic/claude-sonnet-5.5","messages":[{"role":"user","content":"please cmflaggedword now"}]}'
# flagged_entry <label> [curl args...]: like chat_entry, with the flagged prompt.
flagged_entry() {
  local label=$1 code
  shift
  code=$(scn_chat "$FLAGGED_BODY" "$@")
  scn_assert_eq "$label.status" 200 "$code"
  entry_for "$(scn_hdr x-agentcc-request-id)"
}
flagged_entry 5a
scn_assert_jq 5a.content_mode .content_mode full "$SCN_DIR/entry.json"
scn_assert_jq 5a.warning_kept '.metadata.guardrail_warnings | contains("cmflaggedword")' true "$SCN_DIR/entry.json"
SCN_KEY=$NONE_KEY flagged_entry 5b
scn_assert_jq 5b.content_mode .content_mode none "$SCN_DIR/entry.json"
scn_assert_jq 5b.guardrail_triggered '.guardrail_triggered' true "$SCN_DIR/entry.json"
scn_assert_jq 5b.no_warning '.metadata | has("guardrail_warnings")' false "$SCN_DIR/entry.json"
scn_assert_eq 5b.word_nowhere 0 "$(grep -c 'cmflaggedword' "$SCN_DIR/entry.json")"

scn_pass
