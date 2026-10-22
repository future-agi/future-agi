#!/usr/bin/env bash
# 14_catalog.sh: catalog v2 through the real binary, with the managed
# OpenRouter provider's catalog served by the mock's fixtures.
#
#   1. The boot refresh is the first catalog version in the history.
#   2. The mock restarts with one price changed; an admin refresh adds a
#      second version whose diff names the model, and a second refresh in
#      the same minute is refused with 429 and Retry-After.
#   3. The first version's content stays retrievable, at its old price.
#   4. PUT /-/catalog/blocks shows the model as blocked in GET /-/catalog,
#      persists in the snapshot directory, and is not a new version.
#   5. GET /-/catalog/endpoints follows catalog-endpoints-v1, with the
#      OpenRouter source, the 550 bps fee and the mock's ZDR list merged.
#
# Usage: SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<n> bash 14_catalog.sh
#
# set -u only, like the other scenarios: lib.sh's teardown kills processes
# that may already be gone. Every step that can fail checks for it.
set -u
source "$(dirname "$0")/lib.sh"

MODEL=anthropic/claude-sonnet-5.5
BLOCKED=openai/gpt-6-sol
OLD_PRICE=0.000002
NEW_PRICE=0.0000021

# The mock's second fixture set: the common one with MODEL's prompt price
# changed. It lives in the run's scratch dir (scn_up empties SCN_DIR).
FIX_B=$SCN_HOME/scn-fixtures-repriced
rm -rf "$FIX_B"
cp -R "$SCN_ROOT/fixtures/common" "$FIX_B" || scn_fail setup "cannot copy the common fixtures to $FIX_B"
python3 -I - "$FIX_B/models.json" "$MODEL" "$OLD_PRICE" "$NEW_PRICE" <<'PY' || scn_fail setup "cannot reprice $MODEL in $FIX_B"
import json, sys
path, model, old, new = sys.argv[1:]
doc = json.load(open(path, encoding="utf-8"))
entry = next(m for m in doc["data"] if m["id"] == model)
assert entry["pricing"]["prompt"] == old, entry["pricing"]
entry["pricing"]["prompt"] = new
json.dump(doc, open(path, "w", encoding="utf-8"), indent=2)
PY

export SCN_CONFIG_TEMPLATE=$SCN_ROOT/fixtures/catalog/config.yaml
scn_up

# history_len: the number of catalog versions in the history, or
# "HTTP <code>" when the history cannot be read. It runs inside $(...), so
# it reports rather than failing; the assertion on its output fails.
history_len() {
  local code
  code=$(scn_admin GET /-/catalog/history)
  if [ "$code" = 200 ]; then jq '.data | length' <"$SCN_DIR/last.body"; else echo "HTTP $code"; fi
}

# restart_mock <fixtures dir>: the mock again, on the same port, serving dir.
restart_mock() {
  local port=${SCN_MOCK##*:} deadline=$(( $(date +%s) + 15 ))
  kill "$SCN_MOCK_PID" 2>/dev/null || true
  wait "$SCN_MOCK_PID" 2>/dev/null || true
  "$SCN_BIN/agentcc-mockup" --addr "127.0.0.1:$port" --fixtures "$1" >"$SCN_DIR/mock-restarted.log" 2>&1 &
  SCN_MOCK_PID=$!
  until curl -sf -o /dev/null "$SCN_MOCK/__health"; do
    kill -0 "$SCN_MOCK_PID" 2>/dev/null || scn_fail restart_mock "the mock exited: $(tail -5 "$SCN_DIR/mock-restarted.log")"
    [ "$(date +%s)" -lt "$deadline" ] || scn_fail restart_mock "the mock never came back on $SCN_MOCK"
    sleep 0.2
  done
}

scn_check "1. the boot refresh is the first catalog version"
scn_assert_eq 1.history_len 1 "$(history_len)"
FIRST=$(jq -r '.data[0].id' <"$SCN_DIR/last.body")
scn_assert_jq 1.history_source '.data[0].source' live
scn_assert_jq 1.history_models '.data[0].model_count' 3
scn_assert_eq 1.catalog_status 200 "$(scn_admin GET /-/catalog)"
scn_assert_jq 1.catalog_snapshot_id .snapshot_id "$FIRST"
scn_assert_jq 1.catalog_not_stale .stale false
scn_assert_jq 1.model_state "[.models[] | select(.id == \"$MODEL\") | .state][0]" active

scn_check "2. a refresh with one price changed is a new version naming the model"
restart_mock "$FIX_B"
scn_assert_eq 2.refresh_status 202 "$(scn_admin POST /-/catalog/refresh)"
scn_assert_jq 2.refresh_from .snapshot_id "$FIRST"
deadline=$(( $(date +%s) + 15 ))
while [ "$(history_len)" != 2 ]; do
  [ "$(date +%s)" -lt "$deadline" ] || scn_fail 2.history_len "the refresh never added a version: $(cat "$SCN_DIR/last.body")"
  sleep 0.2
done
scn_assert_eq 2.history_len 2 "$(history_len)"
SECOND=$(jq -r '.data[0].id' <"$SCN_DIR/last.body")
scn_assert_jq 2.new_version "(.data[0].id != \"$FIRST\")" true
scn_assert_jq 2.price_changed "(.data[0].diff.price_changed | index(\"$MODEL\") != null)" true
scn_assert_jq 2.only_that_model '.data[0].diff.price_changed_count' 1
scn_assert_jq 2.nothing_added '.data[0].diff.added_count + .data[0].diff.removed_count' 0
scn_assert_eq 2.catalog_status 200 "$(scn_admin GET /-/catalog)"
scn_assert_jq 2.current_is_second .snapshot_id "$SECOND"
scn_assert_eq 2.second_refresh_status 429 "$(scn_admin POST /-/catalog/refresh)"
scn_assert_jq 2.second_refresh_code .error.code catalog_refresh_rate_limited
retry_after=$(scn_hdr Retry-After)
scn_assert_eq 2.retry_after_within_a_minute true "$( [ -n "$retry_after" ] && [ "$retry_after" -ge 1 ] && [ "$retry_after" -le 60 ] && echo true || echo false)"

scn_check "3. the first version stays retrievable at its old price"
scn_assert_eq 3.snapshot_status 200 "$(scn_admin GET "/-/catalog/snapshots/$FIRST")"
scn_assert_jq 3.snapshot_id .id "$FIRST"
scn_assert_jq 3.old_price "[.providers[0].models[] | select(.id == \"$MODEL\") | .prices.prompt][0]" "$OLD_PRICE"
scn_assert_eq 3.current_price_status 200 "$(scn_admin GET "/-/catalog/snapshots/$SECOND")"
scn_assert_jq 3.new_price "[.providers[0].models[] | select(.id == \"$MODEL\") | .prices.prompt][0]" "$NEW_PRICE"
scn_assert_eq 3.unknown_snapshot 404 "$(scn_admin GET /-/catalog/snapshots/000000000000)"

scn_check "4. a runtime block is state=blocked, persisted, and not a new version"
# The body goes through a variable: bash 3.2 brace-expands a JSON literal
# with escaped quotes inside "$(...)".
block_body=$(printf '{"models": ["%s"], "reason": "scn-catalog-block"}' "$BLOCKED")
scn_assert_eq 4.block_status 200 "$(scn_admin PUT /-/catalog/blocks "$block_body")"
scn_assert_eq 4.catalog_status 200 "$(scn_admin GET /-/catalog)"
scn_assert_jq 4.blocked_state "[.models[] | select(.id == \"$BLOCKED\") | .state][0]" blocked
scn_assert_jq 4.blocked_reason "[.models[] | select(.id == \"$BLOCKED\") | .state_reason][0]" scn-catalog-block
scn_assert_jq 4.others_active "[.models[] | select(.id == \"$MODEL\") | .state][0]" active
scn_assert_jq 4.same_version .snapshot_id "$SECOND"
scn_assert_jq 4.persisted "[.blocks[] | select(.model == \"$BLOCKED\")] | length" 1 "$SCN_DIR/catalog/blocks.json"
scn_assert_eq 4.history_len 2 "$(history_len)"

scn_check "5. endpoints follow catalog-endpoints-v1"
scn_assert_eq 5.endpoints_status 200 "$(scn_admin GET "/-/catalog/endpoints?model=$MODEL")"
cp "$SCN_DIR/last.body" "$SCN_DIR/endpoints.json"
schema_problems=$(python3 -I - "$SCN_GATEWAY_DIR/../api_contracts/gateway/examples/catalog-endpoints-v1.schema.json" \
  "$SCN_DIR/endpoints.json" <<'PY' || echo "the schema validator failed"
# A subset of JSON Schema, enough for the contract goldens; an unsupported keyword
# is reported as a problem rather than skipped.
import json, re, sys
schema = json.load(open(sys.argv[1], encoding="utf-8"))
doc = json.load(open(sys.argv[2], encoding="utf-8"))
KNOWN = {"$schema", "$id", "title", "description", "$defs", "type", "properties", "required",
         "additionalProperties", "items", "$ref", "pattern", "enum", "minimum", "minLength"}
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}
def is_type(t, v):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, TYPES[t])
def check(s, v, at):
    out = ["%s: unsupported keyword %s" % (at, k) for k in s if k not in KNOWN]
    if "$ref" in s:
        return out + check(schema["$defs"][s["$ref"].split("/")[-1]], v, at)
    types = s.get("type")
    if types is not None:
        types = types if isinstance(types, list) else [types]
        if not any(is_type(t, v) for t in types):
            return out + ["%s: %r is not %s" % (at, v, types)]
    if isinstance(v, dict):
        props = s.get("properties", {})
        out += ["%s: missing %s" % (at, r) for r in s.get("required", []) if r not in v]
        for k, val in v.items():
            if k in props:
                out += check(props[k], val, at + "." + k)
            elif s.get("additionalProperties") is False:
                out.append("%s: %s is not allowed" % (at, k))
    if isinstance(v, list) and "items" in s:
        for i, item in enumerate(v):
            out += check(s["items"], item, "%s[%d]" % (at, i))
    if isinstance(v, str) and "pattern" in s and not re.search(s["pattern"], v):
        out.append("%s: %r does not match %s" % (at, v, s["pattern"]))
    if "enum" in s and v not in s["enum"]:
        out.append("%s: %r not in %s" % (at, v, s["enum"]))
    return out
print("; ".join(check(schema, doc, "$")))
PY
)
scn_assert_eq 5.schema_valid "" "$schema_problems"
scn_assert_jq 5.has_endpoints '.data.endpoints | length >= 1' true "$SCN_DIR/endpoints.json"
scn_assert_jq 5.source '.data.endpoints[0].source' openrouter "$SCN_DIR/endpoints.json"
scn_assert_jq 5.upstream_fee_bps '.data.endpoints[0].upstream_fee_bps' 550 "$SCN_DIR/endpoints.json"
scn_assert_jq 5.zdr_merged '.data.endpoints[0].zdr' true "$SCN_DIR/endpoints.json"
scn_assert_jq 5.data_collection '.data.endpoints[0].data_collection' deny "$SCN_DIR/endpoints.json"
scn_assert_jq 5.endpoint_price '.data.endpoints[0].pricing.prompt' "$NEW_PRICE" "$SCN_DIR/endpoints.json"
scn_assert_eq 5.stats_status 200 "$(scn_admin GET /-/catalog/stats)"
scn_assert_jq 5.stats_endpoints '.endpoints >= 1 and .per_source.openrouter.models == 3' true

scn_pass
