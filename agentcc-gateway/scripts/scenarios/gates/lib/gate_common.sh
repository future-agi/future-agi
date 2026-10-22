# shellcheck shell=bash
# Shared setup of the scenario gate checks (gates/*.sh).
# Source it from a gate script after setting, when needed,
# SCN_CONFIG_TEMPLATE and SCN_MOCK_FIXTURES:
#
#   source "$(dirname "$0")/lib/gate_common.sh"
#
# Like run_all.sh, it refuses to run without SCN_REDIS and a positive
# SCN_REDIS_DB (managed billing fails closed without Redis). Scratch goes to
# $GATE_HOME/scn-breaker (GATE_HOME defaults to ${TMPDIR:-/tmp}/agentcc-gates).
if [ -z "${SCN_REDIS:-}" ] || ! [[ "${SCN_REDIS_DB:-}" =~ ^[1-9][0-9]*$ ]]; then
  echo "$(basename "$0"): SCN_REDIS and a non-zero SCN_REDIS_DB are required (got" \
    "SCN_REDIS='${SCN_REDIS:-}' SCN_REDIS_DB='${SCN_REDIS_DB:-}')" >&2
  exit 1
fi
GATES_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PRICED_FIXTURES=$GATES_ROOT/fixtures/catalog-priced
GATE_HOME=${GATE_HOME:-${TMPDIR:-/tmp}/agentcc-gates}
export SCN_RUN=${SCN_RUN:-gate-breaker}
export SCN_HOME=${SCN_HOME:-$GATE_HOME/scn-breaker}
mkdir -p "$SCN_HOME"
# shellcheck source=../../lib.sh
source "$GATES_ROOT/../lib.sh"

GATE_INTERNAL_KEY=scn-test-key-gate-internal

# gate_entry <request id>: wait (30 s) for the request's log entry at the mock
# and keep it in $SCN_DIR/entry.json; fails the check when it never arrives.
gate_entry() {
  local deadline=$(( $(date +%s) + 30 ))
  while :; do
    curl -sS "$SCN_MOCK/__logs" | jq --arg id "$1" '[.logs[] | select(.request_id == $id)] | first // empty' >"$SCN_DIR/entry.json"
    [ -s "$SCN_DIR/entry.json" ] && return 0
    [ "$(date +%s)" -ge "$deadline" ] && scn_fail "entry $1" "no request log entry for $1"
    sleep 0.5
  done
}
