#!/usr/bin/env bash
# Self-test of the scenario harness itself:
#  1. 00_baseline.sh with one expected value flipped must make the runner exit
#     1, name that assertion, and stop there.
#  2. scn_gateway_log holds the gateway's startup lines, and with
#     SCN_OTEL_EXPORTER=stdout one chat call writes a span line to it (and the
#     baseline, run without it, wrote none). scn_key and scn_logs work.
#  3. scn_grant pushes cumulative, versioned grants to PUT credits/granted
#     (served here by a stand-in that records each push), and fails when a
#     grant is not applied. The stand-in must answer within 10 s, and it is
#     stopped on every exit path; its port is left in stand-in.port.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> bash scripts/scenarios/01_selftest.sh
set -u
source "$(dirname "$0")/lib.sh"
# Part 1 runs run_all.sh, which refuses to start without Redis (the same test).
[ -n "${SCN_REDIS:-}" ] && [[ "${SCN_REDIS_DB:-}" =~ ^[1-9][0-9]*$ ]] ||
  scn_fail 0.redis "01_selftest.sh runs run_all.sh, which needs SCN_REDIS and a non-zero SCN_REDIS_DB" \
    "(got SCN_REDIS='${SCN_REDIS:-}' SCN_REDIS_DB='${SCN_REDIS_DB:-}')"

FLIP=3.usage_cost
scn_check "1. a flipped expectation fails the runner at that assertion"
out=$(SCN_SELFTEST_FLIP=$FLIP SCN_FAIL_FILE= bash "$SCN_ROOT/run_all.sh" 00_baseline.sh 2>&1)
status=$?
echo "$out" | tail -n 3 | sed 's/^/  | /'
scn_assert_eq 1.runner_exit 1 "$status"
scn_assert_eq 1.runner_names_it "run_all: FAILED 00_baseline.sh at assertion '$FLIP'" "$(echo "$out" | tail -n 1)"
scn_assert_eq 1.stopped_there 0 "$(echo "$out" | grep -c '^=== 4\.')"
baseline_log="$SCN_HOME/scn-run/00_baseline/gateway.log"
scn_assert_eq 1.no_spans_without_otel 0 "$(grep -c '"span_id"' "$baseline_log")"

scn_check "2. the gateway log, with OTel spans on stdout"
SCN_OTEL_EXPORTER=stdout
SCN_SKIP_BUILD=1
scn_up
scn_assert_eq 2.startup_logged 1 "$(scn_gateway_log | grep -c '"msg":"agentcc gateway initializing"')"
scn_assert_eq 2.otel_on 1 "$(scn_gateway_log | grep -c '"msg":"otel enabled"')"
scn_org_config "$SCN_ORG" '{"managed_models":{"enabled":true}}'
scn_grant "$SCN_ORG" 1
scn_assert_eq 2.chat_status 200 "$(scn_chat '{"model":"anthropic/claude-sonnet-5.5","messages":[{"role":"user","content":"hi"}]}')"
request_id=$(scn_hdr x-agentcc-request-id)
span=""
for _ in $(seq 1 25); do
  span=$(scn_gateway_log | grep '"span_id"' | grep -F "$request_id" | head -n 1)
  [ -n "$span" ] && break
  sleep 0.2
done
scn_assert_eq 2.span_line_for_the_call yes "$( [ -n "$span" ] && echo yes || echo "no span line naming $request_id")"
scn_assert_eq 2.span_is_json yes "$(echo "$span" | jq -e '.trace_id and .span_id and .name' >/dev/null 2>&1 && echo yes || echo no)"
# A plain label: POST /-/keys refuses every reserved metadata name except
# org_id and allow_policy_override, and team is reserved.
beta_key=$(scn_key org-beta '{"metadata":{"tenant":"t1"}}')
scn_assert_eq 2.key_for_other_org 403 "$(SCN_KEY=$beta_key scn_chat '{"model":"anthropic/claude-sonnet-5.5","messages":[{"role":"user","content":"hi"}]}')"
scn_logs 1 >"$SCN_DIR/logs.json" || scn_fail 2.logs_delivered "no request log reached the mock: $(cat "$SCN_DIR/logs.json")"
scn_assert_jq 2.logged_the_call "[.logs[] | select(.request_id == \"$request_id\")] | length" 1 "$SCN_DIR/logs.json"

scn_check "3. scn_grant: cumulative totals, a rising version, and a refused grant fails"
fake_port=$(_scn_free_port)
python3 -I - "$fake_port" "$SCN_DIR/grants.jsonl" <<'FAKE' >"$SCN_DIR/stand-in.log" 2>&1 &
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
port, log = int(sys.argv[1]), sys.argv[2]
stored = {}
class Granted(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        self.send_response(204)
        self.end_headers()
    def do_PUT(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        org = self.path.split("/")[3]
        applied = body["version"] > stored.get(org, 0)
        if applied:
            stored[org] = body["version"]
        with open(log, "a") as f:
            f.write(json.dumps(dict(body, path=self.path, auth=self.headers["Authorization"])) + "\n")
        out = json.dumps({"applied": applied, "granted_version": stored[org]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)
HTTPServer(("127.0.0.1", port), Granted).serve_forever()
FAKE
fake_pid=$!
echo "$fake_port" >"$SCN_DIR/stand-in.port"
stop_stand_in() {
  [ -n "${fake_pid:-}" ] || return 0
  kill "$fake_pid" 2>/dev/null
  wait "$fake_pid" 2>/dev/null
  fake_pid=""
}
trap 'stop_stand_in; scn_down' EXIT # scn_up's trap was scn_down alone
stand_in_deadline=$(( $(date +%s) + 10 ))
until curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$fake_port/"; do
  kill -0 "$fake_pid" 2>/dev/null || scn_fail 3.stand_in_ready "the stand-in exited: $(tail -n 3 "$SCN_DIR/stand-in.log")"
  [ "$(date +%s)" -ge "$stand_in_deadline" ] &&
    scn_fail 3.stand_in_ready "the stand-in did not answer on port $fake_port within 10 s"
  sleep 0.1
done
(SCN_GW=http://127.0.0.1:$fake_port && scn_grant org-g 1 && scn_grant org-g 2.5)
jq -s . "$SCN_DIR/grants.jsonl" >"$SCN_DIR/grants.json"
scn_assert_jq 3.grants_sent length 2 "$SCN_DIR/grants.json"
scn_assert_jq 3.first '.[0] | [.path, .version, .granted_total_micros, .settled_spend_floor_micros] | @csv' \
  '"/-/orgs/org-g/credits/granted",1,1000000,0' "$SCN_DIR/grants.json"
scn_assert_jq 3.second '.[1] | [.version, .granted_total_micros, .auth] | @csv' \
  "2,3500000,\"Bearer $SCN_ADMIN_TOKEN\"" "$SCN_DIR/grants.json"
echo 0 >"$SCN_DIR/grant-version-org-g" # the next push repeats version 1, which the stand-in refuses
(SCN_FAIL_FILE= && SCN_GW=http://127.0.0.1:$fake_port && scn_grant org-g 1) 2>/dev/null
scn_assert_eq 3.refused_grant_fails 1 "$?"
jq -s . "$SCN_DIR/grants.jsonl" >"$SCN_DIR/grants.json"
scn_assert_jq 3.refused_push_was_version_1 '[length, .[2].version] | @csv' 3,1 "$SCN_DIR/grants.json"
stop_stand_in

scn_pass
