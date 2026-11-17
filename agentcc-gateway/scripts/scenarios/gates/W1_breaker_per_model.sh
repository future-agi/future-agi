#!/usr/bin/env bash
# Gate check: circuits are per
# model once the failover record site and the per-model BreakerKey are both merged.
#  1. sc/18_g5_breaker.sh on the integrated binary (through run_all.sh):
#     25 requests to a failing managed model open its circuits; another model
#     on the same provider still answers; the failing model's next request is
#     refused with 503 without reaching the upstream; the provider view is
#     half-open and healthy.
#  2. The gate's own Failover.Execute case,
#     internal/routing/w1_gate_breaker_execute_test.go
#     (TestW1ExecuteOpensOnlyTheFailingModelsCircuit): 25 failures through
#     Execute on one model open that model's circuits (5 calls per target,
#     then none) and no other; must report PASS.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<n> bash gates/W1_breaker_per_model.sh
set -u
GATES=$(cd "$(dirname "$0")" && pwd)
SC=$(cd "$GATES/.." && pwd)
GW=$(cd "$SC/../.." && pwd)
GATE_HOME=${GATE_HOME:-$HOME/.futureagi-local/gateway-program/lanes/X6-gates}
SCN_TIMEOUT=${GATE_SCENARIO_TIMEOUT:-900}
mkdir -p "$GATE_HOME"
if [ -z "${SCN_REDIS:-}" ] || ! [[ "${SCN_REDIS_DB:-}" =~ ^[1-9][0-9]*$ ]]; then
  echo "W1_breaker_per_model: SCN_REDIS and a non-zero SCN_REDIS_DB are required" >&2
  exit 1
fi
status=0

echo "### W1_breaker_per_model part 1: 18_g5_breaker.sh"
SCN_LANE=${SCN_LANE:-x6-w1-gate} SCN_HOME=${SCN_HOME:-$GATE_HOME/scn-w1} \
  perl -e 'alarm shift; exec @ARGV or die "exec: $!"' "$SCN_TIMEOUT" bash "$SC/run_all.sh" 18_g5_breaker.sh
rc=$?
if [ $rc -eq 0 ]; then echo "W1_breaker_per_model part 1: PASS"; else echo "W1_breaker_per_model part 1: FAIL (exit $rc)"; status=1; fi

echo "### W1_breaker_per_model part 2: Failover.Execute opens only the failing model's circuits"
log=$GATE_HOME/w1-breaker-execute.log
(cd "$GW" && go test ./internal/routing -run '^TestW1ExecuteOpensOnlyTheFailingModelsCircuit$' -count=1 -v) >"$log" 2>&1
rc=$?
if [ $rc -eq 0 ] && grep -q '^--- PASS: TestW1ExecuteOpensOnlyTheFailingModelsCircuit ' "$log"; then
  echo "W1_breaker_per_model part 2: PASS"
else
  echo "W1_breaker_per_model part 2: FAIL (exit $rc, log $log)"
  status=1
fi

if [ $status -eq 0 ]; then echo "W1_breaker_per_model: PASS"; else echo "W1_breaker_per_model: FAIL"; fi
exit $status
