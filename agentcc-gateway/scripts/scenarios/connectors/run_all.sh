#!/usr/bin/env bash
# The connector track's scenario runner (CONNECTORS-PLAN AM-C14, C5.2): the
# frozen scenarios/run_all.sh globs only [0-9][0-9]_*.sh and every two-digit
# number there is taken, so the C-track's scenarios live here and run here.
# It runs every NN_*.sh in this directory in order (or the ones named as
# arguments, each with the arguments it needs given by SCN_CN_ARGS_<NN>, see
# below) and stops at the first that fails, naming its failed assertion. Exit
# status: 0 when all pass, 1 otherwise.
#
# Redis is required, as in the frozen runner: SCN_REDIS (host:port) and
# SCN_REDIS_DB (the lane's own Redis DB, a positive integer, never 0).
#
# A scenario that needs arguments gets them from SCN_CN_ARGS_<NN>; unset, the
# runner passes the defaults listed in default_args below (93: --catalog
# testdata), one run per line: 94 runs twice, with no arguments (CN10's
# example catalog behind the real gateway) and with --catalog catalog/connectors
# --apps all (every committed K2 cassette of the repository catalog replayed,
# IR-CN14c-1). Lanes that add a scenario here (CN10, CN24, CN26, CN30, CN33)
# add its default to default_args in the same commit.
set -u
if [ -z "${SCN_REDIS:-}" ] || ! [[ "${SCN_REDIS_DB:-}" =~ ^[1-9][0-9]*$ ]]; then
  echo "connectors/run_all: SCN_REDIS and SCN_REDIS_DB are required (got SCN_REDIS='${SCN_REDIS:-}'" \
    "SCN_REDIS_DB='${SCN_REDIS_DB:-}'): set SCN_REDIS=127.0.0.1:16379 and SCN_REDIS_DB to your" \
    "lane's Redis DB, never 0" >&2
  exit 1
fi
DIR=$(cd "$(dirname "$0")" && pwd)

default_args() { # NN: the arguments a scenario runs with by default, one run per line
  case "$1" in
    93) echo "--catalog testdata" ;;
    94) printf '%s\n' "" "--catalog catalog/connectors --apps all" ;;
    *) echo "" ;;
  esac
}

scripts=()
if [ $# -gt 0 ]; then
  for name in "$@"; do scripts+=("$DIR/$(basename "$name")"); done
else
  for f in "$DIR"/[0-9][0-9]_*.sh; do [ -e "$f" ] && scripts+=("$f"); done
fi
if [ ${#scripts[@]} -eq 0 ]; then
  echo "connectors/run_all: no scenario scripts found" >&2
  exit 1
fi

home=${SCN_HOME:-$HOME/.futureagi-local/gateway-program/lanes/${SCN_LANE:-local}}
mkdir -p "$home/scn-run"
fail_file="$home/scn-run/connectors-run_all.$$.fail"
trap 'rm -f "$fail_file"' EXIT
echo "connectors/run_all: Redis $SCN_REDIS db $SCN_REDIS_DB"
nruns=0
for script in "${scripts[@]}"; do
  name=$(basename "$script")
  [ -f "$script" ] || { echo "connectors/run_all: FAILED $name: no such scenario"; exit 1; }
  nn=${name%%_*}
  var=SCN_CN_ARGS_$nn
  runs=${!var-$(default_args "$nn")}
  while IFS= read -r args; do
    nruns=$((nruns + 1))
    : >"$fail_file"
    echo "### connectors/run_all: $name $args"
    # shellcheck disable=SC2086 # the arguments are words
    SCN_FAIL_FILE="$fail_file" bash "$script" $args </dev/null
    status=$?
    if [ $status -ne 0 ] || [ -s "$fail_file" ]; then
      assertion=$(head -n 1 "$fail_file")
      echo "connectors/run_all: FAILED $name${args:+ $args} at assertion '${assertion:-<none named; exit status $status>}'"
      exit 1
    fi
  done <<<"$runs"
done
echo "connectors/run_all: all ${#scripts[@]} scenarios passed ($nruns runs)"
