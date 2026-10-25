#!/usr/bin/env bash
# Runs the scenario scripts (every NN_*.sh here, in order, or the ones named
# as arguments) and stops at the first that fails, naming its failed
# assertion. Exit status: 0 when all pass, 1 otherwise.
#
# Redis is required. SCN_REDIS (host:port, such as 127.0.0.1:16379) and
# SCN_REDIS_DB (a Redis DB of your own: a positive integer, never 0) must
# both be set, or the runner exits 1 naming them before it runs anything.
# Managed billing fails closed without Redis (managed.require_redis), so a
# memory-mode run would fail every managed check; and the runner picks no
# default DB, because a guessed DB could be shared with another run. A
# scenario script run on its own (bash 00_baseline.sh) still accepts
# memory mode.
set -u
if [ -z "${SCN_REDIS:-}" ] || ! [[ "${SCN_REDIS_DB:-}" =~ ^[1-9][0-9]*$ ]]; then
  echo "run_all: SCN_REDIS and SCN_REDIS_DB are required (got SCN_REDIS='${SCN_REDIS:-}'" \
    "SCN_REDIS_DB='${SCN_REDIS_DB:-}'): set SCN_REDIS=127.0.0.1:16379 and SCN_REDIS_DB to your" \
    "own Redis DB, never 0" >&2
  exit 1
fi
source "$(dirname "$0")/lib.sh"

scripts=()
if [ $# -gt 0 ]; then
  for name in "$@"; do scripts+=("$SCN_ROOT/$(basename "$name")"); done
else
  for f in "$SCN_ROOT"/[0-9][0-9]_*.sh; do [ -e "$f" ] && scripts+=("$f"); done
fi
if [ ${#scripts[@]} -eq 0 ]; then
  echo "run_all: no scenario scripts found" >&2
  exit 1
fi

mkdir -p "$SCN_HOME/scn-run"
fail_file="$SCN_HOME/scn-run/run_all.$$.fail"
trap 'rm -f "$fail_file"' EXIT
echo "run_all: Redis $SCN_REDIS db $SCN_REDIS_DB"
for script in "${scripts[@]}"; do
  name=$(basename "$script")
  [ -f "$script" ] || { echo "run_all: FAILED $name: no such scenario"; exit 1; }
  : >"$fail_file"
  echo "### run_all: $name"
  SCN_FAIL_FILE="$fail_file" bash "$script"
  status=$?
  if [ $status -ne 0 ] || [ -s "$fail_file" ]; then
    assertion=$(head -n 1 "$fail_file")
    echo "run_all: FAILED $name at assertion '${assertion:-<none named; exit status $status>}'"
    exit 1
  fi
done
echo "run_all: all ${#scripts[@]} scenarios passed"
