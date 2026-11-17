#!/usr/bin/env bash
# MCP isolation wave-gate integration check.
# Proves, on the integrated tree:
#   1. Named Go tests report --- PASS by name, from
#      fixtures/W3b/go-tests.txt: every merged change's named acceptance
#      (identity through the upstream client, and the meter), the merge's own tests (each red before its
#      commit), and the earlier MCP-session tests rerun after the legacy-session
#      change; then the tenancy and legacy-session -race acceptance over internal/mcp and
#      internal/redisstate on the test Redis.
#   2. The MCP scenarios 62-71 through run_all.sh with SCN_WAVE_GATE=1, so
#      their gate-only steps run (65's key isolation and calls, the tenancy
#      proof; 66's virtual servers; 68's list_changed and replay; 70's
#      attribution).
#   3. W3b_isolation_live.sh: two orgs with a server named github on their own
#      mocks, Django's vault push, sessions and anonymous methods, both eras,
#      the admin playground's metering and both orgs' tool_call events
#      ingested into Django under the right org.
#   4. Django (GATE_DJANGO_TEST, a test-runner script): the merged changes'
#      acceptance files, the identity files and the merge's own, every test in
#      fixtures/W3b/django-tests.txt asserted PASSED by name.
#   5. Unless GATE_SKIP_EARLIER=1: W3b_capabilities.sh (the capability rows that do
#      not need search, the managed byok_only refusal and the secret-migration check), then every
#      earlier gate: W3_billing_agent_money.sh with the benchmark baseline
#      (default 8b8abc0dac, the merged money tree, allocs budget 0), which runs
#      W2c_capability_core.sh, W2b_mcp_seams.sh and W0b_seams.sh.
#
# Usage: W3b_mcp_isolation.sh [benchmark-baseline]
# Environment: SCN_REDIS, SCN_REDIS_DB (required); GATE_HOME; GATE_DJANGO_TEST
# and GATE_DJANGO_MANAGE (parts 3-5 need both); GATE_SKIP_EARLIER.
# Exit status: 0 when every part passes, 1 otherwise.
set -u

BENCH_BASELINE=${1:-8b8abc0dac43f787b81b665a0422335eff8730e7}
GATES=$(cd "$(dirname "$0")" && pwd)
SC=$(cd "$GATES/.." && pwd)
GW=$(cd "$SC/../.." && pwd)
GATE_HOME=${GATE_HOME:-$HOME/.futureagi-local/gateway-program/lanes/X6-gates}
SCN_TIMEOUT=${GATE_SCENARIO_TIMEOUT:-2400}
mkdir -p "$GATE_HOME"

if [ -z "${SCN_REDIS:-}" ] || ! [[ "${SCN_REDIS_DB:-}" =~ ^[1-9][0-9]*$ ]]; then
  echo "W3b_mcp_isolation: SCN_REDIS and a non-zero SCN_REDIS_DB are required" >&2
  exit 1
fi

status=0
join() { local IFS='|'; echo "$*"; }

# --- part 1: named Go tests ---------------------------------------------------
echo "### W3b_mcp_isolation part 1: named Go tests (lane acceptance, integrator rows, round 4's session tests)"
NAMES=()
while read -r owner name; do
  case "$owner" in '' | '#'*) continue ;; esac
  NAMES+=("$name")
done <"$GATES/fixtures/W3b/go-tests.txt"
p1=$GATE_HOME/w3b-part1-go.log
(cd "$GW" && TEST_REDIS_ADDR=${TEST_REDIS_ADDR:-$SCN_REDIS} TEST_REDIS_DB=${TEST_REDIS_DB:-$SCN_REDIS_DB} \
  go test ./... -run "^($(join "${NAMES[@]}"))\$" -count=1 -v) >"$p1" 2>&1
rc=$?
missing=0
for name in "${NAMES[@]}"; do
  grep -Eq -- "^\s*--- PASS: ${name} " "$p1" || { echo "  missing PASS for $name"; missing=1; }
done
if [ $rc -eq 0 ] && [ $missing -eq 0 ] && ! grep -Eq -- "--- SKIP: ($(join "${NAMES[@]}")) " "$p1"; then
  echo "W3b_mcp_isolation part 1a: PASS (${#NAMES[@]} named tests)"
else
  echo "W3b_mcp_isolation part 1a: FAIL (exit $rc, missing $missing; log $p1)"
  grep -E -- "^(--- FAIL|FAIL|panic)" "$p1" | head -20
  status=1
fi
p1b=$GATE_HOME/w3b-part1b-race.log
(cd "$GW" && TEST_REDIS_ADDR=${TEST_REDIS_ADDR:-$SCN_REDIS} TEST_REDIS_DB=${TEST_REDIS_DB:-$SCN_REDIS_DB} \
  go test -race ./internal/mcp/... ./internal/redisstate -count=1) >"$p1b" 2>&1
rc=$?
if [ $rc -eq 0 ] && ! grep -q 'DATA RACE' "$p1b"; then
  echo "W3b_mcp_isolation part 1b: PASS (internal/mcp/... and internal/redisstate under -race)"
else
  echo "W3b_mcp_isolation part 1b: FAIL (exit $rc; log $p1b)"
  grep -E -- "^(--- FAIL|FAIL|WARNING: DATA RACE)" "$p1b" | head -10
  status=1
fi

# --- part 2: the MCP scenarios at the wave gate -----------------------------------
echo "### W3b_mcp_isolation part 2: 62-71 with SCN_WAVE_GATE=1"
SCN_WAVE_GATE=1 SCN_LANE=${SCN_LANE:-x6-w3b-gate} SCN_HOME=${SCN_HOME:-$GATE_HOME/scn} \
  perl -e 'alarm shift; exec @ARGV or die "exec: $!"' "$SCN_TIMEOUT" \
  bash "$SC/run_all.sh" 62_m4_mcpmock.sh 63_m5_stdio.sh 64_m8_identity.sh 65_m9_tenancy.sh 66_m10_vservers.sh \
  67_m11_protocol_2026.sh 68_m12_legacy_sessions.sh 69_m13_upstream.sh 70_m15_meter.sh 71_m17_probe.sh
rc=$?
if [ $rc -eq 0 ]; then echo "W3b_mcp_isolation part 2: PASS"; else echo "W3b_mcp_isolation part 2: FAIL (exit $rc)"; status=1; fi

# --- part 3: the isolation live check -----------------------------------------------
echo "### W3b_mcp_isolation part 3: W3b_isolation_live.sh"
log=$GATE_HOME/w3b-part3-isolation-live.log
if [ -z "${GATE_DJANGO_MANAGE:-}" ]; then
  echo "W3b_mcp_isolation part 3: FAIL (GATE_DJANGO_MANAGE is not set)"
  status=1
elif GATE_HOME=$GATE_HOME bash "$GATES/W3b_isolation_live.sh" >"$log" 2>&1; then
  echo "W3b_mcp_isolation part 3: PASS ($(grep -Eo '[0-9]+ assertions' "$log" | tail -1))"
else
  echo "W3b_mcp_isolation part 3: FAIL (log $log)"
  grep -E "SCN FAIL" "$log" | head -3
  status=1
fi

# --- part 4: Django rows ------------------------------------------------------------
echo "### W3b_mcp_isolation part 4: Django rows (identity, vault and ingest acceptance; the merge's own)"
p4=$GATE_HOME/w3b-part4-django.log
if [ -z "${GATE_DJANGO_TEST:-}" ]; then
  echo "W3b_mcp_isolation part 4: FAIL (GATE_DJANGO_TEST is not set)"
  status=1
else
  FILES=()
  while IFS= read -r line; do
    case "$line" in '' | '#'*) continue ;; esac
    f=${line%%::*}
    [[ " ${FILES[*]-} " == *" $f "* ]] || FILES+=("$f")
  done <"$GATES/fixtures/W3b/django-tests.txt"
  "$GATE_DJANGO_TEST" "${FILES[@]}" -p no:cacheprovider -q -rA >"$p4" 2>&1
  rc=$?
  missing=0 total=0
  while IFS= read -r line; do
    case "$line" in '' | '#'*) continue ;; esac
    total=$((total + 1))
    file=${line%%::*} fn=${line##*::}
    grep -Eq "^PASSED ${file}::([A-Za-z0-9_]+::)?${fn}(\[| |$)" "$p4" || { echo "  missing PASSED for $line"; missing=1; }
  done <"$GATES/fixtures/W3b/django-tests.txt"
  if [ $rc -eq 0 ] && [ $missing -eq 0 ]; then
    echo "W3b_mcp_isolation part 4: PASS ($total named; $(grep -Eo '[0-9]+ passed' "$p4" | tail -1))"
  else
    echo "W3b_mcp_isolation part 4: FAIL (exit $rc; log $p4)"
    grep -E "^(FAILED|ERROR) " "$p4" | head -10
    status=1
  fi
fi

# --- part 5: capabilities and every earlier gate ----------------------------------------
if [ "${GATE_SKIP_EARLIER:-}" = 1 ]; then
  echo "### W3b_mcp_isolation part 5: skipped (GATE_SKIP_EARLIER=1; run W3b_capabilities.sh and the earlier gates separately)"
else
  echo "### W3b_mcp_isolation part 5a: W3b_capabilities.sh"
  if GATE_HOME=$GATE_HOME bash "$GATES/W3b_capabilities.sh"; then
    echo "W3b_mcp_isolation part 5a: PASS"
  else
    echo "W3b_mcp_isolation part 5a: FAIL"
    status=1
  fi
  echo "### W3b_mcp_isolation part 5b: W3_billing_agent_money.sh $BENCH_BASELINE (every earlier gate)"
  if GATE_HOME=$GATE_HOME bash "$GATES/W3_billing_agent_money.sh" "$BENCH_BASELINE"; then
    echo "W3b_mcp_isolation part 5b: PASS"
  else
    echo "W3b_mcp_isolation part 5b: FAIL"
    status=1
  fi
fi

if [ $status -eq 0 ]; then echo "W3b_mcp_isolation: PASS"; else echo "W3b_mcp_isolation: FAIL"; fi
exit $status
