#!/usr/bin/env bash
# CW1 wave-gate integration check (CONNECTORS-PLAN 2.5 and 3.4, integrator-owned; CN6 IR-1).
#
# Proves, on the integrated CW1 tree (int/connectors):
#   1. The generated connector catalog is current: `agentcc-catalog check`
#      (dist/** and the Django copy futureagi/agentcc/connector_catalog/
#      catalog.json byte-exact against the records), and `check
#      --sources-only`, the data lanes' gate (record checks, version bumps and
#      every emitter's refusals). go test runs only the second on the
#      repository, on purpose (data lanes never regenerate dist/**), so the
#      full check runs here at every wave merge (and in the gateway CI job).
#   2. Named Go tests report --- PASS by name and never --- SKIP, from
#      fixtures/CW1/go-tests.txt: CN6's, CN7's and CN12's acceptance, CN10's
#      (CW2) and the integrator's tests of each merge; then CN8's acceptance 2,
#      its whole package (go test ./internal/connectorcatalog/conformance/...).
#   3. The connector scenarios through connectors/run_all.sh (CN8's
#      93_cn_conformance.sh with its default --catalog testdata, the L3 run
#      through the real gateway chain; CN10's 94_cn_rest_runtime.sh, the K2
#      runtime behind the gateway with a per-call bearer, no arguments, then
#      again with --catalog catalog/connectors --apps all: every committed K2
#      cassette of the repository catalog replayed through the runtime, CW2d);
#      CN8-IR-5: lanes that add a scenario there add its default_args in the
#      same commit.
#   4. CN13's runner tests (python3 -m pytest scripts/connector-smoke/tests),
#      with GATE_PYTHON (a Python with pytest and PyYAML; default python3):
#      every test in fixtures/CW1/connector-smoke-tests.txt, CN13's four
#      acceptance names, asserted PASSED by name (AM-C22) in every
#      parametrization, none of them SKIPPED (AM-C23 (b)), the missing names
#      counted, and none failed. They never make a live call: without keys the
#      runner skips.
#   5. Django (GATE_DJANGO_TEST, a lane-test script): every test in
#      fixtures/CW1/django-tests.txt asserted PASSED by name (CN9's, CN11's and
#      CN22's (CW2) acceptance and the integrator's).
#
# The R-track gates (W3b_mcp_isolation.sh and earlier) are the R line's: this
# line was cut from W3b's tree and its merge reruns them.
#
# Usage: CW1_connectors.sh
# Environment:
#   SCN_REDIS, SCN_REDIS_DB  required: 127.0.0.1:16379 (or the lane's Redis)
#                            and a positive DB number, never 0. Part 2 passes
#                            them as TEST_REDIS_ADDR and TEST_REDIS_DB unless
#                            those are set.
#   GATE_HOME                scratch dir (default lanes/X6-gates under the
#                            gateway program dir; never /tmp).
#   GATE_DJANGO_TEST         part 5's pytest wrapper (required for part 5). It
#                            must export FUTURE_AGI_LICENSE_URL as a 127.0.0.1
#                            URL, or part 5 fails without running it.
#   GATE_PYTHON              part 4's Python (default python3).
#   GATE_PARTS               the parts to run, such as "1 2" (default: all).
#   GATE_SCENARIO_TIMEOUT    part 3's limit in seconds (default 1800).
# Exit status: 0 when every part run passes, 1 otherwise.
set -u

GATES=$(cd "$(dirname "$0")" && pwd)
SC=$(cd "$GATES/.." && pwd)
GW=$(cd "$SC/../.." && pwd)
REPO=$(cd "$GW/.." && pwd)
GATE_HOME=${GATE_HOME:-$HOME/.futureagi-local/gateway-program/lanes/X6-gates}
SCN_TIMEOUT=${GATE_SCENARIO_TIMEOUT:-1800}
PARTS=" ${GATE_PARTS:-1 2 3 4 5} "
mkdir -p "$GATE_HOME"
# Django's ee/licensing activation and heartbeat, and its telemetry, default to
# production hosts when these are unset: every gate keeps them on a closed local
# port (R1 gate review P3, 2026-10-19), and part 5 refuses a wrapper that does
# not pin the license URL itself.
export FUTURE_AGI_LICENSE_URL=http://127.0.0.1:9 FUTURE_AGI_TELEMETRY_URL=http://127.0.0.1:9

if [ -z "${SCN_REDIS:-}" ] || ! [[ "${SCN_REDIS_DB:-}" =~ ^[1-9][0-9]*$ ]]; then
  echo "CW1_connectors: SCN_REDIS and a non-zero SCN_REDIS_DB are required (got" \
    "SCN_REDIS='${SCN_REDIS:-}' SCN_REDIS_DB='${SCN_REDIS_DB:-}')" >&2
  exit 1
fi

status=0
join() { local IFS='|'; echo "$*"; }
want() { [[ "$PARTS" == *" $1 "* ]]; }

# --- part 1: the generated catalog is current ----------------------------------
if want 1; then
  echo "### CW1_connectors part 1: agentcc-catalog check and check --sources-only"
  p1=$GATE_HOME/cw1-part1-catalog.log
  : >"$p1"
  ok=1
  for args in "check" "check --sources-only"; do
    echo "## agentcc-catalog $args" >>"$p1"
    # shellcheck disable=SC2086 # the arguments are words
    (cd "$GW" && go run ./cmd/agentcc-catalog $args) >>"$p1" 2>&1 || ok=0
  done
  if [ $ok -eq 1 ]; then
    echo "CW1_connectors part 1: PASS (dist/** and the Django copy are current; the records pass every emitter)"
  else
    echo "CW1_connectors part 1: FAIL (log $p1)"
    grep -v '^## ' "$p1" | head -10
    status=1
  fi
fi

# --- part 2: named Go tests, then CN8's package --------------------------------
if want 2; then
  echo "### CW1_connectors part 2: named Go tests (lane acceptance and the integrator's), CN8's package"
  NAMES=()
  while read -r owner name; do
    case "$owner" in '' | '#'*) continue ;; esac
    NAMES+=("$name")
  done <"$GATES/fixtures/CW1/go-tests.txt"
  p2=$GATE_HOME/cw1-part2-go.log
  (cd "$GW" && TEST_REDIS_ADDR=${TEST_REDIS_ADDR:-$SCN_REDIS} TEST_REDIS_DB=${TEST_REDIS_DB:-$SCN_REDIS_DB} \
    go test ./... -run "^($(join "${NAMES[@]}"))\$" -count=1 -v) >"$p2" 2>&1
  rc=$?
  missing=0
  for name in "${NAMES[@]}"; do
    grep -Eq -- "^\s*--- PASS: ${name} " "$p2" || { echo "  missing PASS for $name"; missing=$((missing + 1)); }
  done
  # A named test, or any subtest of one (TestX/case), reported SKIP fails the
  # part (AM-C23 (b), as part 4).
  skipped=$(grep -Eo -- "--- SKIP: ($(join "${NAMES[@]}"))(/[^ ]*)? " "$p2" | sort -u)
  [ -z "$skipped" ] || echo "$skipped" | sed 's/^/  skipped: /'
  if [ $rc -eq 0 ] && [ $missing -eq 0 ] && [ -z "$skipped" ]; then
    echo "CW1_connectors part 2a: PASS (${#NAMES[@]} named tests, no subtest skipped)"
  else
    echo "CW1_connectors part 2a: FAIL (exit $rc, $missing missing, $(printf '%s' "$skipped" | grep -c .) skipped; log $p2)"
    grep -E -- "^(--- FAIL|FAIL|panic)" "$p2" | head -20
    status=1
  fi
  p2b=$GATE_HOME/cw1-part2b-conformance.log
  (cd "$GW" && go test ./internal/connectorcatalog/conformance/... -count=1) >"$p2b" 2>&1
  rc=$?
  if [ $rc -eq 0 ]; then
    echo "CW1_connectors part 2b: PASS ($(grep -c '^ok' "$p2b") packages ok)"
  else
    echo "CW1_connectors part 2b: FAIL (exit $rc; log $p2b)"
    grep -E -- "^(--- FAIL|FAIL|panic)" "$p2b" | head -10
    status=1
  fi
fi

# --- part 3: the connector scenarios --------------------------------------------
if want 3; then
  echo "### CW1_connectors part 3: connectors/run_all.sh"
  SCN_LANE=${SCN_LANE:-x6-cw1-gate} SCN_HOME=${SCN_HOME:-$GATE_HOME/scn} \
    perl -e 'alarm shift; exec @ARGV or die "exec: $!"' "$SCN_TIMEOUT" \
    bash "$SC/connectors/run_all.sh"
  rc=$?
  if [ $rc -eq 0 ]; then
    echo "CW1_connectors part 3: PASS"
  else
    [ $rc -eq 142 ] && echo "CW1_connectors part 3: killed after ${SCN_TIMEOUT}s"
    echo "CW1_connectors part 3: FAIL (exit $rc)"
    status=1
  fi
fi

# --- part 4: CN13's live-smoke runner tests (no live call), its acceptance by name -------
# Every named test must report PASSED for each parametrization pytest ran, and
# none SKIPPED, XFAIL, XPASS, FAILED or ERROR (AM-C23 (b)): -rA's summary prints
# a skip as "SKIPPED [n] file:line", with no test name, so the outcomes are read
# from -v's one line per test ("<file>::<fn>[<id>] OUTCOME"). The missing names
# are counted.
if want 4; then
  echo "### CW1_connectors part 4: scripts/connector-smoke/tests (CN13's acceptance by name)"
  p4=$GATE_HOME/cw1-part4-connector-smoke.log
  (cd "$REPO" && PYTHONDONTWRITEBYTECODE=1 "${GATE_PYTHON:-python3}" -m pytest scripts/connector-smoke/tests \
    -v -p no:cacheprovider -rA) >"$p4" 2>&1
  rc=$?
  missing=0 notpassed=0 total=0
  while IFS= read -r line; do
    case "$line" in '' | '#'*) continue ;; esac
    total=$((total + 1))
    file=${line%%::*} fn=${line##*::}
    id="${file//./\\.}::${fn}(\\[.*\\])?"
    if ! grep -Eq "^${id} PASSED( |$)" "$p4"; then
      echo "  missing PASSED for $line"
      missing=$((missing + 1))
    fi
    bad=$(grep -Eo "^${id} (SKIPPED|XFAIL|XPASS|FAILED|ERROR)" "$p4" | sort -u)
    if [ -n "$bad" ]; then
      echo "  not PASSED for $line:"
      echo "$bad" | sed 's/^/    /'
      notpassed=$((notpassed + $(echo "$bad" | wc -l)))
    fi
  done <"$GATES/fixtures/CW1/connector-smoke-tests.txt"
  if [ $rc -eq 0 ] && [ $total -gt 0 ] && [ $missing -eq 0 ] && [ $notpassed -eq 0 ] &&
    grep -Eq '[0-9]+ passed' "$p4" && ! grep -Eq '[0-9]+ (failed|error)' "$p4"; then
    echo "CW1_connectors part 4: PASS ($total named, every parametrization PASSED; $(grep -Eo '[0-9]+ passed' "$p4" | tail -1))"
  else
    echo "CW1_connectors part 4: FAIL (exit $rc, $total named, $missing missing, $notpassed not PASSED; log $p4)"
    grep -E "^(FAILED|ERROR) " "$p4" | head -10
    tail -5 "$p4"
    status=1
  fi
fi

# --- part 5: Django rows ------------------------------------------------------------
if want 5; then
  echo "### CW1_connectors part 5: Django rows (CN9, CN11, CN22 and the integrator's)"
  p5=$GATE_HOME/cw1-part5-django.log
  if [ -z "${GATE_DJANGO_TEST:-}" ]; then
    echo "CW1_connectors part 5: FAIL (GATE_DJANGO_TEST is not set)"
    status=1
  elif ! grep -Eq 'FUTURE_AGI_LICENSE_URL=["'"'"']?http://127\.0\.0\.1[:/]' "$GATE_DJANGO_TEST"; then
    echo "CW1_connectors part 5: FAIL ($GATE_DJANGO_TEST does not set FUTURE_AGI_LICENSE_URL to a" \
      "127.0.0.1 URL, so its tests could reach the production license API; not run)"
    status=1
  else
    FILES=()
    while IFS= read -r line; do
      case "$line" in '' | '#'*) continue ;; esac
      f=${line%%::*}
      [[ " ${FILES[*]-} " == *" $f "* ]] || FILES+=("$f")
    done <"$GATES/fixtures/CW1/django-tests.txt"
    # -v prints one "<file>::[<Class>::]<fn>[<id>] OUTCOME" line per test, so
    # a skipped parametrization of a named test is seen by name (AM-C23 (b)).
    "$GATE_DJANGO_TEST" "${FILES[@]}" -p no:cacheprovider -v -rA >"$p5" 2>&1
    rc=$?
    missing=0 notpassed=0 total=0
    while IFS= read -r line; do
      case "$line" in '' | '#'*) continue ;; esac
      total=$((total + 1))
      file=${line%%::*} fn=${line##*::}
      id="${file//./\\.}::([A-Za-z0-9_]+::)?${fn}(\\[.*\\])?"
      grep -Eq "^PASSED ${file}::([A-Za-z0-9_]+::)?${fn}(\[| |$)" "$p5" || { echo "  missing PASSED for $line"; missing=$((missing + 1)); }
      bad=$(grep -Eo "^${id} (SKIPPED|XFAIL|XPASS|FAILED|ERROR)" "$p5" | sort -u)
      if [ -n "$bad" ]; then
        echo "  not PASSED for $line:"
        echo "$bad" | sed 's/^/    /'
        notpassed=$((notpassed + $(echo "$bad" | wc -l)))
      fi
    done <"$GATES/fixtures/CW1/django-tests.txt"
    if [ $rc -eq 0 ] && [ $missing -eq 0 ] && [ $notpassed -eq 0 ]; then
      echo "CW1_connectors part 5: PASS ($total named, every parametrization PASSED; $(grep -Eo '[0-9]+ passed' "$p5" | tail -1))"
    else
      echo "CW1_connectors part 5: FAIL (exit $rc, $missing missing, $notpassed not PASSED; log $p5)"
      grep -E "^(FAILED|ERROR) " "$p5" | head -10
      status=1
    fi
  fi
fi

if [ $status -eq 0 ]; then echo "CW1_connectors: PASS"; else echo "CW1_connectors: FAIL"; fi
exit $status
