#!/usr/bin/env bash
# Gate check: a staged rollout buckets on the session id the parse step sets
# (sticky_by=session).
#  1. With routing.rollout at 50% and sticky_by=session, one key of the
#     fixture's org sends a model "auto" request for each of the 40 fixture
#     sessions (fixtures/rollout/ids.json "sessions", buckets precomputed as
#     fnv1a32(org + session id) % 100), as x-agentcc-session-id. Exactly the
#     20 sessions below 50 are logged candidate (the rollout's version and the
#     candidate's auto preference, cost); the other 20 stable (v41, quality).
#  2. Sticky: a second request in four of the sessions lands in the same
#     variant as the first, and every entry logs its session id.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<n> bash gates/rollout_session.sh
set -u
GATES_DIR=$(cd "$(dirname "$0")" && pwd)
source "$GATES_DIR/lib/gate_common.sh"
FIXTURE=$SCN_ROOT/fixtures/rollout/ids.json
scn_up

ORG=$(jq -r .org "$FIXTURE")
PERCENT=$(jq -r .percent "$FIXTURE")
AUTO='{"model":"auto","messages":[{"role":"user","content":"What is the capital of France?"}]}'
EXPECTED=$SCN_DIR/expected.tsv # request id, session id, expected variant, pass
: >"$EXPECTED"
CONFIG=$(jq -nc --argjson p "$PERCENT" '{managed_models: {enabled: true}, routing: {auto: {preference: "quality"},
  rollout: {version: "v42", percent: $p, sticky_by: "session", started_at: "2026-10-15T00:00:00Z", routing: {auto: {preference: "cost"}}}},
  meta: {version: 41, config_id: "gate-rollout-session", pushed_at: "2026-10-15T00:00:00Z"}}')

scn_check "0. the org's rollout and one key"
scn_assert_eq 0.sessions 40 "$(jq '.sessions | length' "$FIXTURE")"
scn_assert_eq 0.sessions_below 20 "$(jq --argjson p "$PERCENT" '[.sessions[] | select(.bucket < $p)] | length' "$FIXTURE")"
scn_org_config "$ORG" "$CONFIG"
scn_grant "$ORG" 5
KEY=$(scn_key "$ORG")

session_chat() { # session id, bucket, pass
  local want=stable
  [ "$2" -lt "$PERCENT" ] && want=candidate
  scn_assert_eq "1.status.$1.$3" 200 "$(SCN_KEY=$KEY scn_chat "$AUTO" -H "x-agentcc-session-id: $1")"
  printf '%s\t%s\t%s\t%s\n' "$(scn_hdr x-agentcc-request-id)" "$1" "$want" "$3" >>"$EXPECTED"
}

scn_check "1. 40 sessions, one key: the split follows the session buckets"
while IFS=$'\t' read -r sid bucket; do session_chat "$sid" "$bucket" 1; done \
  < <(jq -r '.sessions[] | "\(.id)\t\(.bucket)"' "$FIXTURE")

scn_check "2. a second request in four sessions stays in its variant"
while IFS=$'\t' read -r sid bucket; do session_chat "$sid" "$bucket" 2; done \
  < <(jq -r --argjson p "$PERCENT" '[.sessions[] | select(.bucket < $p)][0:2] + [.sessions[] | select(.bucket >= $p)][0:2] | .[] | "\(.id)\t\(.bucket)"' "$FIXTURE")

WANT=$(wc -l <"$EXPECTED" | tr -d ' ')
scn_logs "$WANT" 30 >"$SCN_DIR/logs.json" || scn_fail 3.logs "only $(jq '.logs | length' "$SCN_DIR/logs.json") of $WANT log entries arrived"

scn_check "3. every entry carries its session's variant, version and policy"
entry() { jq -r --arg id "$1" "[.logs[] | select(.request_id == \$id)] | if length == 1 then .[0] | $2 else \"<\(length) entries>\" end" "$SCN_DIR/logs.json"; }
while IFS=$'\t' read -r req sid want pass; do
  if [ "$want" = candidate ]; then version=v42 pref=cost; else version=v41 pref=quality; fi
  scn_assert_eq "3.$sid.$pass.session" "$sid" "$(entry "$req" .session_id)"
  scn_assert_eq "3.$sid.$pass.variant" "$want" "$(entry "$req" .routing.policy_variant)"
  scn_assert_eq "3.$sid.$pass.version" "$version" "$(entry "$req" .routing.policy_version)"
  scn_assert_eq "3.$sid.$pass.preference" "$pref" "$(entry "$req" .metadata.auto_preference)"
done <"$EXPECTED"

scn_check "4. of the 40 first-pass entries, exactly the fixture's 20 sessions below $PERCENT are candidate"
first=$(jq -Rn '[inputs | split("\t") | select(.[3] == "1") | .[0]]' "$EXPECTED")
logged=$(jq -r --argjson ids "$first" '[.logs[] | select(.request_id as $r | $ids | index($r) != null)
  | select(.routing.policy_variant == "candidate") | .session_id] | sort | join(",")' "$SCN_DIR/logs.json")
fixture=$(jq -r --argjson p "$PERCENT" '[.sessions[] | select(.bucket < $p) | .id] | sort | join(",")' "$FIXTURE")
scn_assert_eq 4.candidate_sessions "$fixture" "$logged"

scn_pass
