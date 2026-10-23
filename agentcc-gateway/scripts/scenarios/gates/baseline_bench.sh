#!/usr/bin/env bash
# Gate check: scenarios and the benchmark guard against a pin.
#
# Proves, on this tree:
#   1. 00_baseline.sh and 01_selftest.sh pass on this binary;
#   2. the benchmark guard holds against the pin: interleaved runs of
#      BenchmarkChatCompletion* from the pin and from this tree, both built
#      with this tree's Go toolchain, judged by lib/bench_guard.py.
#
# Usage: baseline_bench.sh <pin-sha>
#        baseline_bench.sh release   the pin, allocation budget and unpinned
#                                 benchmarks from lib/release_bench_pin.txt
#                                 (the environment variables below override them)
# Environment:
#   SCN_REDIS, SCN_REDIS_DB  required, passed to the scenarios: run_all.sh exits
#                            1 without them. Use 127.0.0.1:16379 and a positive
#                            DB number, never 0.
#   GATE_HOME                scratch dir (default ${TMPDIR:-/tmp}/agentcc-gates).
#   GATE_BENCH_ROUNDS        interleaved rounds (default 20; each runs
#                            -test.count 2 per side, so 40 samples per side).
#                            Run the gate alone: a concurrent test suite on the
#                            host makes the timings noisy.
#   GATE_SCENARIO_TIMEOUT    seconds before the scenario run is killed
#                            (default 900), so a hung readiness loop fails
#                            the gate instead of hanging it.
#   GATE_SKIP_BENCH=1        skip part 2 (report it as skipped).
#   GATE_BENCH_ALLOCS_BUDGET an approved allocs/op budget over the pin
#                            (default 0), passed to bench_guard.py
#                            --allocs-budget.
#   GATE_BENCH_UNPINNED      comma-separated benchmark names the head may have
#                            and the pin may lack (bench_guard.py --unpinned):
#                            reported, not judged, until the pin has them.
# Exit status: 0 when every part passes, 1 otherwise.
set -u

PIN=${1:-}
if [ -z "$PIN" ]; then
  echo "usage: $0 <pin-sha>|release" >&2
  exit 1
fi
GATES=$(cd "$(dirname "$0")" && pwd)
if [ "$PIN" = release ]; then
  # Read as data: only these three keys, never executed.
  PIN_FILE=$GATES/lib/release_bench_pin.txt
  rel_value() { sed -n "s/^$1=\([A-Za-z0-9_,]*\)\$/\1/p" "$PIN_FILE" | tail -1; }
  PIN=$(rel_value RELEASE_BENCH_PIN)
  rel_budget=$(rel_value RELEASE_BENCH_ALLOCS_BUDGET)
  rel_unpinned=$(rel_value RELEASE_BENCH_UNPINNED)
  if ! [[ "$PIN" =~ ^[0-9a-f]{40}$ ]] || ! [[ "$rel_budget" =~ ^[0-9]+$ ]]; then
    echo "baseline_bench: $PIN_FILE must set RELEASE_BENCH_PIN (a full commit sha) and RELEASE_BENCH_ALLOCS_BUDGET" >&2
    exit 1
  fi
  GATE_BENCH_ALLOCS_BUDGET=${GATE_BENCH_ALLOCS_BUDGET:-$rel_budget}
  GATE_BENCH_UNPINNED=${GATE_BENCH_UNPINNED-$rel_unpinned}
  echo "baseline_bench: release pin ${PIN:0:10}, allocs budget +$GATE_BENCH_ALLOCS_BUDGET, unpinned [${GATE_BENCH_UNPINNED}] ($PIN_FILE)"
fi
SC=$(cd "$GATES/.." && pwd)
GW=$(cd "$SC/../.." && pwd)
REPO=$(git -C "$GW" rev-parse --show-toplevel)
GATE_HOME=${GATE_HOME:-${TMPDIR:-/tmp}/agentcc-gates}
ROUNDS=${GATE_BENCH_ROUNDS:-20}
SCN_TIMEOUT=${GATE_SCENARIO_TIMEOUT:-900}
mkdir -p "$GATE_HOME"

if [ -z "${SCN_REDIS:-}" ] || ! [[ "${SCN_REDIS_DB:-}" =~ ^[1-9][0-9]*$ ]]; then
  echo "baseline_bench: SCN_REDIS and a non-zero SCN_REDIS_DB are required (got" \
    "SCN_REDIS='${SCN_REDIS:-}' SCN_REDIS_DB='${SCN_REDIS_DB:-}'); run_all.sh refuses without them" >&2
  exit 1
fi
git -C "$REPO" cat-file -e "$PIN^{commit}" 2>/dev/null || {
  echo "baseline_bench: pin $PIN is not a commit in $REPO" >&2
  exit 1
}

status=0

# --- part 1: scenarios on the integrated binary ----------------------------
echo "### baseline_bench part 1: 00_baseline.sh and 01_selftest.sh"
SCN_RUN=${SCN_RUN:-gate-core} SCN_HOME=${SCN_HOME:-$GATE_HOME/scn} \
  perl -e 'alarm shift; exec @ARGV or die "exec: $!"' "$SCN_TIMEOUT" \
  bash "$SC/run_all.sh" 00_baseline.sh 01_selftest.sh
rc=$?
if [ $rc -eq 0 ]; then
  echo "baseline_bench part 1: PASS"
else
  [ $rc -eq 142 ] && echo "baseline_bench part 1: killed after ${SCN_TIMEOUT}s"
  echo "baseline_bench part 1: FAIL (exit $rc)"
  status=1
fi

# --- part 2: benchmark guard against the pin -------------------------------
if [ "${GATE_SKIP_BENCH:-}" = "1" ]; then
  echo "### baseline_bench part 2: SKIPPED (GATE_SKIP_BENCH=1)"
else
  echo "### baseline_bench part 2: benchmark guard, $ROUNDS interleaved rounds against pin ${PIN:0:10}"
  bench=$GATE_HOME/bench
  rm -rf "$bench" && mkdir -p "$bench/pin-tree"
  git -C "$REPO" archive "$PIN" agentcc-gateway | tar -x -C "$bench/pin-tree"
  # Both sides are built with this tree's Go (its go.mod toolchain under the
  # default GOTOOLCHAIN=auto), not each with its own: the pin's go.mod may
  # name an older Go, and a Go release moves allocs/op and ns/op on both
  # sides (go1.26.10 adds one allocation per chat completion over go1.25.0),
  # while the guard and its allocs budget measure the code.
  bench_go=$(cd "$GW" && go env GOVERSION)
  bench_go=${bench_go%% *}
  if ! (cd "$bench/pin-tree/agentcc-gateway" && GOTOOLCHAIN=$bench_go go test -c -o "$bench/pin.test" ./internal/server) ||
     ! (cd "$GW" && GOTOOLCHAIN=$bench_go go test -c -o "$bench/head.test" ./internal/server); then
    echo "baseline_bench part 2: FAIL (could not build the benchmark binaries with $bench_go)"
    status=1
  else
    echo "baseline_bench part 2: both sides built with $bench_go: $(go version "$bench/pin.test" | sed 's#^.*/##'), $(go version "$bench/head.test" | sed 's#^.*/##')"
    : >"$bench/pin.txt"; : >"$bench/head.txt"
    run_side() { # side dir
      (cd "$2/internal/server" && "$bench/$1.test" -test.run '^$' -test.bench '^BenchmarkChatCompletion' \
        -test.benchmem -test.count 2 2>&1) >>"$bench/$1.txt"
    }
    for r in $(seq 1 "$ROUNDS"); do
      # Alternate which side goes first so host drift cancels out.
      if [ $((r % 2)) -eq 1 ]; then
        run_side pin "$bench/pin-tree/agentcc-gateway"; run_side head "$GW"
      else
        run_side head "$GW"; run_side pin "$bench/pin-tree/agentcc-gateway"
      fi
    done
    unpinned=()
    [ -n "${GATE_BENCH_UNPINNED:-}" ] && unpinned=(--unpinned "$GATE_BENCH_UNPINNED")
    if python3 -I "$GATES/lib/bench_guard.py" --allocs-budget "${GATE_BENCH_ALLOCS_BUDGET:-0}" ${unpinned[@]+"${unpinned[@]}"} \
      "$bench/pin.txt" "$bench/head.txt"; then
      echo "baseline_bench part 2: PASS"
    else
      echo "baseline_bench part 2: FAIL (raw runs in $bench)"
      status=1
    fi
  fi
fi

if [ $status -eq 0 ]; then echo "baseline_bench: PASS"; else echo "baseline_bench: FAIL"; fi
exit $status
