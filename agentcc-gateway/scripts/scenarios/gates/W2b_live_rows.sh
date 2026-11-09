#!/usr/bin/env bash
# W2b gate live rows (plan section 3, integrator-owned), on the real binary
# and the X0 mock:
#  1. IC-3: a chat request for google/imagen-3.0-generate-002, chosen because
#     catalog/deprecations.json lists it as already shut down on 2025-11-10
#     (unlike gemini-2.5-flash-image, which serves until 2027-03-15). The mock's
#     catalog carries no expiration date for it: the calendar alone must yield
#     410 model_expired naming the replacement and no upstream call; its
#     replacement is served.
#  2. IC-4: a live GET /-/orgs/{id}/credits body carries org_id and validates
#     against credits-v1.schema.json (with the golden's customer part).
#  3. M1x startup refusals: the gateway exits non-zero, before serving, with
#     routing.scheduled.enabled: true, and with a FutureAGI guardrail rule
#     that has no eval_id.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<n> bash gates/W2b_live_rows.sh
set -u
GATES_DIR=$(cd "$(dirname "$0")" && pwd)
SCN_MOCK_FIXTURES=$GATES_DIR/fixtures/W2b/mock
export SCN_LANE=${SCN_LANE:-x6-w2b-gate}
GATE_HOME=${GATE_HOME:-$HOME/.futureagi-local/gateway-program/lanes/X6-gates}
export SCN_HOME=${SCN_HOME:-$GATE_HOME/scn-w2b}
source "$GATES_DIR/lib/w2_common.sh"
CONTRACTS=$(cd "$GATES_DIR/../../../../api_contracts/gateway/examples" && pwd)
DJ_PY=$(cd "$GATES_DIR/../../../../futureagi" && pwd)/.venv/bin/python
scn_up

ORG=$SCN_ORG
GONE='{"model":"google/imagen-3.0-generate-002","messages":[{"role":"user","content":"draw a cat"}]}'
LIVE='{"model":"google/gemini-3.1-flash-image","messages":[{"role":"user","content":"draw a cat"}]}'
scn_grant "$ORG" 5
scn_org_config "$ORG" '{"managed_models":{"enabled":true}}'

scn_check "1. IC-3: a model past its calendar shutdown is 410 model_expired, with no upstream call"
before=$(w2_upstream_chats)
scn_assert_eq 1.status 410 "$(scn_chat "$GONE")"
scn_assert_jq 1.code .error.code model_expired
scn_assert_jq 1.names_replacement '.error.message | contains("gemini-3.1-flash-image")' true
scn_assert_eq 1.no_upstream_call "$before" "$(w2_upstream_chats)"
scn_assert_eq 1.replacement_served 200 "$(scn_chat "$LIVE")"
scn_assert_eq 1.replacement_reached_upstream "$((before + 1))" "$(w2_upstream_chats)"

scn_check "2. IC-4: the live credits body carries org_id and validates against credits-v1"
scn_assert_eq 2.status 200 "$(scn_admin GET "/-/orgs/$ORG/credits")"
scn_assert_jq 2.org_id .org_id "$ORG"
valid=$("$DJ_PY" -I - "$CONTRACTS" "$SCN_DIR/last.body" <<'PY'
import json, sys
from jsonschema import Draft202012Validator
schema = json.load(open(sys.argv[1] + "/credits-v1.schema.json"))
golden = json.load(open(sys.argv[1] + "/credits-v1.json"))
doc = {"admin": json.load(open(sys.argv[2])), "customer": golden["customer"]}
errors = [e.message for e in Draft202012Validator(schema).iter_errors(doc)]
errors += [e.message for e in Draft202012Validator(schema).iter_errors(golden)]
print("ok" if not errors else "; ".join(errors))
PY
)
scn_assert_eq 2.validates ok "$valid"

scn_check "3. M1x: the gateway refuses to start on routing.scheduled.enabled and a FutureAGI rule with no eval_id"
refuses() { # <name> <python edit of the rendered config dict>: start must fail with the message
  local name=$1 edit=$2 want=$3 cfg="$SCN_DIR/refuse-$1.yaml" out="$SCN_DIR/refuse-$1.log" rc
  "$DJ_PY" -I - "$SCN_DIR/config.yaml" "$cfg" "$edit" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1]))
exec(sys.argv[3])
yaml.safe_dump(cfg, open(sys.argv[2], "w"))
PY
  env -u AGENTCC_CONFIG_FILE perl -e 'alarm 20; exec @ARGV' "$SCN_BIN/agentcc" --config "$cfg" >"$out" 2>&1
  rc=$?
  scn_assert_eq "3.$name.exits_nonzero" true "$([ $rc -ne 0 ] && [ $rc -ne 142 ] && echo true || echo false)"
  scn_assert_eq "3.$name.says_why" true "$(grep -q "$want" "$out" && echo true || echo false)"
}
refuses scheduled 'cfg.setdefault("routing", {}).setdefault("scheduled", {})["enabled"] = True' 'routing.scheduled.enabled must be false'
refuses futureagi_no_eval 'cfg["guardrails"] = {"enabled": True, "rules": [{"name": "fi", "stage": "pre", "action": "block", "config": {"provider": "futureagi", "api_key": "k", "secret_key": "s"}}]}' 'missing eval_id'

scn_pass
