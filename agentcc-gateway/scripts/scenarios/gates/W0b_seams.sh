#!/usr/bin/env bash
# Seams wave-gate integration check.
#
# Proves, on the integrated tree:
#   1. The call-order and late-bound-deps tests pass (hook order on the
#      non-stream, stream and failover paths, parse before effective config,
#      finalizers, the response header buffer, Handlers.deps), and so do the
#      wiring-order tests in cmd/agentcc. Each named test must report PASS, so
#      a renamed test cannot pass by matching nothing.
#   2. The race run has no -skip from this gate on (the a2a race is fixed):
#      go test -race ./internal/server/... ./internal/a2a/... ./cmd/agentcc/...
#   3. 02_x0b_mock_ext.sh passes on the integrated binary.
#   4. gates/W0_baseline.sh <base>: 00_baseline.sh and 01_selftest.sh, and
#      the benchmark guard (lib/bench_guard.py) against the baseline, so both
#      halves together. Every later gate reruns every earlier check.
#
# Usage: W0b_seams.sh [w0-base-sha]   (default: the baseline e72006cbc0)
# Environment:
#   SCN_REDIS, SCN_REDIS_DB  required (run_all.sh refuses without them): use
#                            127.0.0.1:16379 and a positive DB number, never 0.
#   GATE_HOME                scratch dir (default lanes/X6-gates under the
#                            gateway program dir; never /tmp).
#   GATE_BENCH_ROUNDS, GATE_SCENARIO_TIMEOUT, GATE_SKIP_BENCH,
#   GATE_BENCH_ALLOCS_BUDGET passed to W0_baseline.sh (see its header). The
#                            benchmark runs last; run the gate with no other
#                            test suite on the host.
# Exit status: 0 when every part passes, 1 otherwise.
set -u

BASE=${1:-e72006cbc0a8c0d3d029d8212f14935c3bc66810}
GATES=$(cd "$(dirname "$0")" && pwd)
SC=$(cd "$GATES/.." && pwd)
GW=$(cd "$SC/../.." && pwd)
GATE_HOME=${GATE_HOME:-$HOME/.futureagi-local/gateway-program/lanes/X6-gates}
SCN_TIMEOUT=${GATE_SCENARIO_TIMEOUT:-900}
mkdir -p "$GATE_HOME"

if [ -z "${SCN_REDIS:-}" ] || ! [[ "${SCN_REDIS_DB:-}" =~ ^[1-9][0-9]*$ ]]; then
  echo "W0b_seams: SCN_REDIS and a non-zero SCN_REDIS_DB are required (got" \
    "SCN_REDIS='${SCN_REDIS:-}' SCN_REDIS_DB='${SCN_REDIS_DB:-}')" >&2
  exit 1
fi

status=0

# expect_passed <log> <test names...>: every named test reported PASS.
expect_passed() {
  local log=$1 name missing=0
  shift
  for name in "$@"; do
    if ! grep -Eq -- "^--- PASS: ${name} " "$log"; then
      echo "  missing PASS for $name"
      missing=1
    fi
  done
  return $missing
}

# --- part 1: call order and late-bound deps --------------------------------
echo "### W0b_seams part 1: hook-order, late-bound-deps and wiring-order tests"
SERVER_TESTS=(TestHookOrderNonStream TestHookOrderStream TestHookOrderFailover TestParseBeforeEffectiveConfig
  TestFinalizersRunOnPanicErrorSuccess TestFinalizersRunOnce TestResponseHeaderBuffer TestInjectedDepsLateBound)
WIRING_TESTS=(TestWiringOrder TestCatalogAndCreditsWiring)
p1=$GATE_HOME/w0b-part1.log
join() { local IFS='|'; echo "$*"; }
(cd "$GW" &&
  go test ./internal/server -run "^($(join "${SERVER_TESTS[@]}"))\$" -count=1 -v &&
  go test ./cmd/agentcc -run "^($(join "${WIRING_TESTS[@]}"))\$" -count=1 -v) >"$p1" 2>&1
rc=$?
if [ $rc -eq 0 ] && expect_passed "$p1" "${SERVER_TESTS[@]}" "${WIRING_TESTS[@]}"; then
  echo "W0b_seams part 1: PASS (${#SERVER_TESTS[@]} server + ${#WIRING_TESTS[@]} wiring tests)"
else
  echo "W0b_seams part 1: FAIL (exit $rc, log $p1)"
  status=1
fi

# --- part 2: race run with no -skip ----------------------------------------
echo "### W0b_seams part 2: go test -race (server, a2a, cmd/agentcc), no -skip"
p2=$GATE_HOME/w0b-part2-race.log
(cd "$GW" && go test -race ./internal/server/... ./internal/a2a/... ./cmd/agentcc/... -count=1) >"$p2" 2>&1
rc=$?
races=$(grep -c "WARNING: DATA RACE" "$p2")
if [ $rc -eq 0 ] && [ "$races" -eq 0 ]; then
  echo "W0b_seams part 2: PASS ($(grep -c '^ok' "$p2") packages ok, 0 DATA RACE)"
else
  echo "W0b_seams part 2: FAIL (exit $rc, $races DATA RACE, log $p2)"
  grep -E "^(--- FAIL|FAIL)" "$p2" | head -20
  status=1
fi

# --- part 3: the mock extension scenario ------------------------------------
echo "### W0b_seams part 3: 02_x0b_mock_ext.sh"
SCN_LANE=${SCN_LANE:-x6-gate} SCN_HOME=${SCN_HOME:-$GATE_HOME/scn} \
  perl -e 'alarm shift; exec @ARGV or die "exec: $!"' "$SCN_TIMEOUT" \
  bash "$SC/run_all.sh" 02_x0b_mock_ext.sh
rc=$?
if [ $rc -eq 0 ]; then
  echo "W0b_seams part 3: PASS"
else
  [ $rc -eq 142 ] && echo "W0b_seams part 3: killed after ${SCN_TIMEOUT}s"
  echo "W0b_seams part 3: FAIL (exit $rc)"
  status=1
fi

# --- part 4: the baseline check against the base (scenarios, benchmark) -----
echo "### W0b_seams part 4: W0_baseline.sh ${BASE:0:10}"
if GATE_HOME=$GATE_HOME bash "$GATES/W0_baseline.sh" "$BASE"; then
  echo "W0b_seams part 4: PASS"
else
  echo "W0b_seams part 4: FAIL"
  status=1
fi

if [ $status -eq 0 ]; then echo "W0b_seams: PASS"; else echo "W0b_seams: FAIL"; fi
exit $status
