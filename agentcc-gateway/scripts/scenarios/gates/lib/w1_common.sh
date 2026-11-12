# shellcheck shell=bash
# Shared setup of the gate checks gates/W1_*.sh.
# Source it from a gate script after setting, when needed,
# SCN_CONFIG_TEMPLATE and SCN_MOCK_FIXTURES:
#
#   source "$(dirname "$0")/lib/w1_common.sh"
#
# Like run_all.sh, it refuses to run without SCN_REDIS and a positive
# SCN_REDIS_DB (managed billing fails closed without Redis). Scratch goes to
# $GATE_HOME/scn-w1 (GATE_HOME defaults to lanes/X6-gates), never /tmp.
if [ -z "${SCN_REDIS:-}" ] || ! [[ "${SCN_REDIS_DB:-}" =~ ^[1-9][0-9]*$ ]]; then
  echo "$(basename "$0"): SCN_REDIS and a non-zero SCN_REDIS_DB are required (got" \
    "SCN_REDIS='${SCN_REDIS:-}' SCN_REDIS_DB='${SCN_REDIS_DB:-}')" >&2
  exit 1
fi
W1_GATES=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
W1_FIXTURES=$W1_GATES/fixtures/W1
GATE_HOME=${GATE_HOME:-$HOME/.futureagi-local/gateway-program/lanes/X6-gates}
export SCN_LANE=${SCN_LANE:-x6-w1-gate}
export SCN_HOME=${SCN_HOME:-$GATE_HOME/scn-w1}
mkdir -p "$SCN_HOME"
# shellcheck source=../../lib.sh
source "$W1_GATES/../lib.sh"

W1_INTERNAL_KEY=scn-test-key-w1-internal

# w1_entry <request id>: wait (30 s) for the request's log entry at the mock
# and keep it in $SCN_DIR/entry.json; fails the check when it never arrives.
w1_entry() {
  local deadline=$(( $(date +%s) + 30 ))
  while :; do
    curl -sS "$SCN_MOCK/__logs" | jq --arg id "$1" '[.logs[] | select(.request_id == $id)] | first // empty' >"$SCN_DIR/entry.json"
    [ -s "$SCN_DIR/entry.json" ] && return 0
    [ "$(date +%s)" -ge "$deadline" ] && scn_fail "entry $1" "no request log entry for $1"
    sleep 0.5
  done
}
