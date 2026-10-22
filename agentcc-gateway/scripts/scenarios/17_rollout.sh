#!/usr/bin/env bash
# Policy version on every decision, and a staged rollout bucketed
# deterministically, through the real gateway binary and the mock upstream.
#  1. Before any rollout, a request is logged with the config's version
#     (v<meta.version>) and the stable variant.
#  2. With routing.rollout at 50% and sticky_by=key, 40 fixture keys each send
#     a model "auto" request. Exactly the 20 keys whose bucket
#     fnv1a32(org + key id) % 100 is below 50 (fixtures/rollout/ids.json) are logged
#     as candidate, with the rollout's version and the candidate's auto
#     preference (cost); the other 20 are stable, with the config's version
#     and stable's preference (quality).
#  3. Sticky: a second request from four of those keys lands in the same
#     variant as the first.
#
# The gateway never honours a client request id (the RequestID middleware,
# internal/middleware/requestid.go, always mints a ULID), so the scenario
# buckets on sticky_by=key, with 40 fixture keys loaded through
# POST /-/keys/sync with fixed ids. gates/rollout_session.sh checks
# sticky_by=session (the fixture's "sessions" list).
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> bash scripts/scenarios/17_rollout.sh
set -u
ROLLOUT_FIXTURE="$(cd "$(dirname "$0")" && pwd)/fixtures/rollout/ids.json"
source "$(dirname "$0")/lib.sh"
scn_up

ORG=$(jq -r .org "$ROLLOUT_FIXTURE")
PERCENT=$(jq -r .percent "$ROLLOUT_FIXTURE")
AUTO='{"model":"auto","messages":[{"role":"user","content":"What is the capital of France?"}]}'
STABLE_VERSION=41
CANDIDATE_VERSION=v42
EXPECTED=$SCN_DIR/expected.tsv # request id, key id, expected variant, pass (1 or 2)
: >"$EXPECTED"

# org_config <rollout json or empty>: the org's config at meta.version 41,
# opted in to managed models, with stable preferring quality.
org_config() {
  python3 -I - "$1" "$STABLE_VERSION" <<'PY'
import json, sys
cfg = {
    "managed_models": {"enabled": True},
    "routing": {"auto": {"preference": "quality"}},
    "meta": {"version": int(sys.argv[2]), "config_id": "scn-rollout-config", "pushed_at": "2026-10-15T00:00:00Z"},
}
if sys.argv[1]:
    cfg["routing"]["rollout"] = json.loads(sys.argv[1])
print(json.dumps(cfg))
PY
}

# key_chat <raw key> <key id> <pass>: one auto request with the key; remembers
# the request id the gateway assigned and the variant the key's bucket implies.
key_chat() {
  local code bucket want
  code=$(SCN_KEY=$1 scn_chat "$AUTO")
  scn_assert_eq "2.status.$2" 200 "$code"
  bucket=$(jq -r --arg id "$2" '.keys[] | select(.id == $id) | .bucket' "$ROLLOUT_FIXTURE")
  if [ "$bucket" -lt "$PERCENT" ]; then want=candidate; else want=stable; fi
  printf '%s\t%s\t%s\t%s\n' "$(scn_hdr x-agentcc-request-id)" "$2" "$want" "$3" >>"$EXPECTED"
}

scn_check "0. load the 40 fixture keys of $ORG with fixed ids"
SYNC=$(python3 -I - "$ROLLOUT_FIXTURE" <<'PY'
import hashlib, json, sys
fx = json.load(open(sys.argv[1], encoding="utf-8"))
print(json.dumps({"keys": [
    {"id": k["id"], "name": k["id"], "key_hash": hashlib.sha256(k["key"].encode()).hexdigest(),
     "metadata": {"org_id": fx["org"]}} for k in fx["keys"]]}))
PY
)
scn_assert_eq 0.sync_status 200 "$(scn_admin POST /-/keys/sync "$SYNC")"
scn_assert_jq 0.keys_loaded .loaded 40
scn_org_config "$ORG" "$(org_config '')"
scn_grant "$ORG" 5

scn_check "1. without a rollout a request is logged as v$STABLE_VERSION, stable"
FIRST_KEY=$(jq -r '.keys[0].key' "$ROLLOUT_FIXTURE")
scn_assert_eq 1.status 200 "$(SCN_KEY=$FIRST_KEY scn_chat "$AUTO")"
PRE_ID=$(scn_hdr x-agentcc-request-id)

scn_check "2. a 50% rollout (sticky_by=key) splits the 40 keys exactly by their fixture buckets"
ROLLOUT=$(jq -nc --arg v "$CANDIDATE_VERSION" --argjson p "$PERCENT" \
  '{version: $v, percent: $p, sticky_by: "key", started_at: "2026-10-15T00:00:00Z", routing: {auto: {preference: "cost"}}}')
scn_org_config "$ORG" "$(org_config "$ROLLOUT")"
while IFS=$'\t' read -r key_id raw; do
  key_chat "$raw" "$key_id" 1
done < <(jq -r '.keys[] | "\(.id)\t\(.key)"' "$ROLLOUT_FIXTURE")

scn_check "3. a second request from four keys stays in its variant"
while IFS=$'\t' read -r key_id raw; do
  key_chat "$raw" "$key_id" 2
done < <(jq -r '[.keys[] | select(.bucket < '"$PERCENT"')][0:2] + [.keys[] | select(.bucket >= '"$PERCENT"')][0:2] | .[] | "\(.id)\t\(.key)"' "$ROLLOUT_FIXTURE")

WANT_ENTRIES=$(( $(wc -l <"$EXPECTED") + 1 ))
scn_logs "$WANT_ENTRIES" 30 >"$SCN_DIR/logs.json" ||
  scn_fail 4.logs "only $(jq '.logs | length' "$SCN_DIR/logs.json") of $WANT_ENTRIES log entries arrived"

scn_check "4. every log entry carries the variant, version and policy of its bucket"
# entry <request id> <jq expr>: a field of that request's log entry.
entry() { jq -r --arg id "$1" "[.logs[] | select(.request_id == \$id)] | if length == 1 then .[0] | $2 else \"<\(length) entries>\" end" "$SCN_DIR/logs.json"; }
scn_assert_eq 4.pre.variant stable "$(entry "$PRE_ID" .routing.policy_variant)"
scn_assert_eq 4.pre.version "v$STABLE_VERSION" "$(entry "$PRE_ID" .routing.policy_version)"
scn_assert_eq 4.pre.preference quality "$(entry "$PRE_ID" .metadata.auto_preference)"
while IFS=$'\t' read -r req_id key_id want _; do
  if [ "$want" = candidate ]; then version=$CANDIDATE_VERSION pref=cost; else version=v$STABLE_VERSION pref=quality; fi
  scn_assert_eq "4.$key_id.$req_id.key" "$key_id" "$(entry "$req_id" .auth_key_id)"
  scn_assert_eq "4.$key_id.$req_id.variant" "$want" "$(entry "$req_id" .routing.policy_variant)"
  scn_assert_eq "4.$key_id.$req_id.version" "$version" "$(entry "$req_id" .routing.policy_version)"
  scn_assert_eq "4.$key_id.$req_id.preference" "$pref" "$(entry "$req_id" .metadata.auto_preference)"
done <"$EXPECTED"

scn_check "5. of the 40 first-pass entries, exactly the fixture's 20 keys below $PERCENT are candidate"
# The first-pass request ids, from expected.tsv; the variants come from the logs.
first_pass=$(jq -Rn '[inputs | split("\t") | select(.[3] == "1") | .[0]]' "$EXPECTED")
scn_assert_eq 5.first_pass_entries 40 "$(jq --argjson ids "$first_pass" '[.logs[] | select(.request_id as $r | $ids | index($r) != null)] | length' "$SCN_DIR/logs.json")"
logged=$(jq -r --argjson ids "$first_pass" '[.logs[] | select(.request_id as $r | $ids | index($r) != null)
  | select(.routing.policy_variant == "candidate") | .auth_key_id] | sort | join(",")' "$SCN_DIR/logs.json")
fixture=$(jq -r --argjson p "$PERCENT" '[.keys[] | select(.bucket < $p) | .id] | sort | join(",")' "$ROLLOUT_FIXTURE")
scn_assert_eq 5.candidate_entries 20 "$(echo "$logged" | tr ',' '\n' | grep -c .)"
scn_assert_eq 5.candidate_keys "$fixture" "$logged"

scn_pass
