#!/usr/bin/env bash
# Gate live rows, on the real binary, the capability mock and the echo
# fixture cards (bound through the tool-catalog binding):
#  1. A managed echo call on a managed_partner fixture vendor, with
#     mcp.managed_tools enabled and an allowing echo/<vendor> glob, reserves
#     and settles exactly one outbox spend record with provider=<vendor>
#     (keyed <request_id>.<attempt>), and nothing stays held.
#  2. A BYOK call writes billing_mode=byok, charges 0 and adds no outbox
#     record, and its request log entry carries the rate-card estimate as its
#     cost (the card's $0.001, no markup).
#  3. The same managed call with mcp.managed_tools.enabled false is 503
#     managed_capability_unavailable and reaches no vendor.
#  4. Startup refusals: the gateway exits non-zero, before serving, with a
#     managed operator provider for a byok_only vendor (brave), with an
#     mcp.managed_servers price_card that names no card, and with
#     managed.tool_markup_percent .nan and with mcp.endpoint /.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<n> bash gates/managed_tools_live_rows.sh
set -u
GATES_DIR=$(cd "$(dirname "$0")" && pwd)
export SCN_RUN=${SCN_RUN:-gate-capability}
GATE_HOME=${GATE_HOME:-${TMPDIR:-/tmp}/agentcc-gates}
export SCN_HOME=${SCN_HOME:-$GATE_HOME/scn-capability}
source "$GATES_DIR/lib/managed_gate_common.sh"
source "$SCN_ROOT/lib_cap.sh"
DJ_PY=$(cd "$GATES_DIR/../../../../futureagi" && pwd)/.venv/bin/python

scn_cap_up
SCN_CONFIG_TEMPLATE="$SCN_ROOT/fixtures/capability-echo/gateway.yaml" SCN_CATALOG_WAIT_SKIP=echo-a,echo-b scn_up

ORG=$SCN_ORG
echo_call() { scn_post /v1/capabilities/echo "$1"; }
org_config() { # <managed_tools enabled true|false> <byok vendors json>
  scn_org_config "$ORG" "$(python3 -I -c '
import json, sys
managed, byok = sys.argv[1] == "true", json.loads(sys.argv[2])
cfg = {"tools": {"capabilities": {"echo": {"enabled": True, "providers": ["echo-a"]}}},
       "mcp": {"managed_tools": {"enabled": managed, "allow": ["echo/echo-a"]}},
       "providers": {v: {"api_key": "scn-test-org-" + v, "enabled": True} for v in byok}}
print(json.dumps(cfg))' "$1" "$2")"
}
outbox() { # the org's outbox as JSON lines: one {field: value} object per record
  managed_redis XRANGE "mc:{$ORG}:outbox" - + | python3 -I -c '
import ast, json, sys
for _id, fields in ast.literal_eval(sys.stdin.read() or "[]"):
    print(json.dumps(dict(zip(fields[::2], fields[1::2]))))'
}
log_entry() { # <request id>: the request's log entry from the mock's /__logs (30 s)
  local rid=$1 entry=null deadline=$(($(date +%s) + 30))
  while [ "$entry" = null ] && [ "$(date +%s)" -lt "$deadline" ]; do
    entry=$(curl -sS "$SCN_MOCK/__logs" | jq -c --arg rid "$rid" '[.logs[] | select(.request_id == $rid)][0]')
    [ "$entry" = null ] && sleep 0.5
  done
  echo "$entry"
}

scn_check "1. a managed echo call settles one outbox spend record with provider=echo-a"
org_config true '[]'
scn_grant "$ORG" 0.01
before=$(managed_outbox "$ORG")
scn_assert_eq 1.status 200 "$(echo_call '{"input":{"row":1}}')"
rid=$(scn_hdr x-agentcc-request-id)
scn_assert_eq 1.billing_mode managed "$(scn_hdr x-agentcc-billing-mode)"
scn_assert_eq 1.one_new_record "$((before + 1))" "$(managed_outbox "$ORG")"
rec=$(outbox | jq -c --arg rid "$rid" 'select(.request_id | startswith($rid))')
scn_assert_eq 1.records_for_request 1 "$(printf '%s\n' "$rec" | grep -c .)"
scn_assert_eq 1.kind spend "$(echo "$rec" | jq -r .kind)"
scn_assert_eq 1.provider echo-a "$(echo "$rec" | jq -r .provider)"
scn_assert_eq 1.request_id "$rid.1" "$(echo "$rec" | jq -r .request_id)"
scn_assert_eq 1.billed_micros 1050 "$(echo "$rec" | jq -r .billed_micros)"
scn_assert_eq 1.held_after 0 "$(managed_credit_field "$ORG" held_micros)"
scn_assert_eq 1.spent_after 1050 "$(managed_credit_field "$ORG" spent_micros)"

scn_check "2. a BYOK call is byok, charges 0, writes no outbox record and logs the rate-card estimate"
org_config true '["echo-a"]'
before=$(managed_outbox "$ORG")
scn_assert_eq 2.status 200 "$(echo_call '{"input":{"row":2}}')"
rid=$(scn_hdr x-agentcc-request-id)
scn_assert_eq 2.billing_mode byok "$(scn_hdr x-agentcc-billing-mode)"
scn_assert_jq 2.charged_usd .usage.charged_usd 0
scn_assert_eq 2.no_outbox_record "$before" "$(managed_outbox "$ORG")"
entry=$(log_entry "$rid")
[ "$entry" != null ] || scn_fail 2.entry "no log entry for request $rid in /__logs"
echo "$entry" >"$SCN_DIR/entry-byok.json"
# The capability core writes rc.Billing (the entry's top-level billing_mode),
# not the billing_mode metadata the chat routing writes.
scn_assert_eq 2.entry_billing_mode byok "$(echo "$entry" | jq -r .billing_mode)"
scn_assert_eq 2.entry_cost 0.001000 "$(echo "$entry" | jq -r .metadata.cost)"
# The response says rate_card; the log entry carries log-entry-v2's name for
# a tool price card's estimate.
scn_assert_eq 2.entry_cost_source price_card "$(echo "$entry" | jq -r .cost_source)"

scn_check "3. managed_tools.enabled false: 503 managed_capability_unavailable, no vendor call"
org_config false '[]'
scn_cap_reset
scn_assert_eq 3.status 503 "$(echo_call '{"input":{"row":3}}')"
scn_assert_jq 3.code .error.code managed_capability_unavailable
scn_assert_eq 3.no_vendor_call 0 "$(scn_cap_received echo-a | jq '.received | length')"

scn_check "4. startup refusals: byok_only managed vendor, dangling price_card, non-finite markup, root mcp.endpoint"
refuses() { # <name> <python edit of the rendered config dict> <message>: start must fail
  local name=$1 edit=$2 want=$3 cfg="$SCN_DIR/refuse-$1.yaml" out="$SCN_DIR/refuse-$1.log" rc
  "$DJ_PY" -I - "$SCN_DIR/config.yaml" "$cfg" "$edit" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1]))
exec(sys.argv[3])
yaml.safe_dump(cfg, open(sys.argv[2], "w"))
PY
  # The Go runtime catches SIGALRM, so an alarm set before exec cannot stop a
  # gateway that starts after all: the wrapper waits 20 s, then sends TERM and
  # exits 142, which counts as a failure to refuse.
  env -u AGENTCC_CONFIG_FILE perl -e '
    my $pid = fork() // die "fork: $!";
    if (!$pid) { exec @ARGV or die "exec: $!" }
    local $SIG{ALRM} = sub { kill "TERM", $pid; waitpid($pid, 0); exit 142 };
    alarm 20; waitpid($pid, 0); alarm 0;
    exit($? & 127 ? 128 + ($? & 127) : $? >> 8)' "$SCN_BIN/agentcc" --config "$cfg" >"$out" 2>&1
  rc=$?
  scn_assert_eq "4.$name.exits_nonzero" true "$([ $rc -ne 0 ] && [ $rc -ne 142 ] && echo true || echo false)"
  scn_assert_eq "4.$name.says_why" true "$(grep -q -- "$want" "$out" && echo true || echo false)"
}
# The seed cards (no tools.catalog_dir) hold brave's byok_only card; a
# tools.catalog_dir replaces them, so this row drops the echo-only one.
refuses byok_only_managed \
  'cfg["tools"].pop("catalog_dir", None); cfg["providers"]["brave"] = {"base_url": "https://api.search.brave.com", "api_key": "scn-test-brave", "api_format": "openai", "managed": True, "auto_discover": False}' \
  'managed use of a byok_only vendor'
refuses dangling_price_card \
  'cfg.setdefault("mcp", {})["managed_servers"] = {"web": {"kind": "remote", "billing": "reserve", "url": "https://web.example/mcp", "price_card": {"capability": "echo", "vendor": "echo-a", "sku": "echo.nope"}}}' \
  'dangling tool price card reference'
refuses nan_tool_markup 'cfg["managed"]["tool_markup_percent"] = float("nan")' 'tool_markup_percent must be a finite number'
refuses root_mcp_endpoint 'cfg.setdefault("mcp", {})["endpoint"] = "/"' 'is refused: it would cover every route'

scn_pass
