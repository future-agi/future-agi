# shellcheck shell=bash
# Shared setup of the W3 gate checks (gates/W3_*.sh, integrator-owned): the
# billing-scenario library (scenarios/lib.sh plus billing-scenarios/lib.sh:
# the real binary, the mock upstream, Django's managed-credit ledger and D3's
# settlement drain on the scenario's Redis DB). Source it from a gate script
# after setting, when needed, SCN_CONFIG_TEMPLATE and SCN_MOCK_FIXTURES:
#
#   source "$(dirname "$0")/lib/w3_common.sh"
#
# Like run_all.sh, it refuses to run without SCN_REDIS and a positive
# SCN_REDIS_DB (managed billing fails closed without Redis). Scratch goes to
# $GATE_HOME/scn-w3 (GATE_HOME defaults to lanes/X6-gates), never /tmp. A
# check that runs Django needs GATE_DJANGO_MANAGE, a manage.py wrapper with
# the gate's database settings (billing-scenarios/lib.sh); Django pushes to
# the scenario gateway and its drain reads SCN_REDIS_DB, whatever REDIS_URL
# the wrapper exports.
if [ -z "${SCN_REDIS:-}" ] || ! [[ "${SCN_REDIS_DB:-}" =~ ^[1-9][0-9]*$ ]]; then
  echo "$(basename "$0"): SCN_REDIS and a non-zero SCN_REDIS_DB are required (got" \
    "SCN_REDIS='${SCN_REDIS:-}' SCN_REDIS_DB='${SCN_REDIS_DB:-}')" >&2
  exit 1
fi
W3_GATES=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
W3_FIXTURES=$W3_GATES/fixtures/W3
GATE_HOME=${GATE_HOME:-$HOME/.futureagi-local/gateway-program/lanes/X6-gates}
export SCN_LANE=${SCN_LANE:-x6-w3-gate}
export SCN_HOME=${SCN_HOME:-$GATE_HOME/scn-w3}
mkdir -p "$SCN_HOME"
# shellcheck source=../../../billing-scenarios/lib.sh
source "$W3_GATES/../../billing-scenarios/lib.sh"

W3_CALL='{"model":"anthropic/claude-sonnet-5.5","max_tokens":40,"messages":[{"role":"user","content":"hi"}]}'
W3_STREAM='{"model":"anthropic/claude-sonnet-5.5","max_tokens":400,"stream":true,"messages":[{"role":"user","content":"hi"}]}'

# w3_need_django: stop unless GATE_DJANGO_MANAGE names the gate's wrapper.
w3_need_django() {
  [ -n "${GATE_DJANGO_MANAGE:-}" ] && [ -f "$GATE_DJANGO_MANAGE" ] ||
    scn_fail django "GATE_DJANGO_MANAGE must name the gate's manage.py wrapper (got '${GATE_DJANGO_MANAGE:-}')"
}

# w3_redis_json <command> [args...]: one Redis command in the scenario's DB;
# prints the reply as JSON (nested lists kept, nil as null).
w3_redis_json() {
  python3 -I - "$SCN_REDIS" "$SCN_REDIS_DB" "$@" <<'PY'
import json, socket, sys
host, port = sys.argv[1].rsplit(":", 1)
sock = socket.create_connection((host, int(port)), timeout=5)
rd = sock.makefile("rb")
def reply():
    line = rd.readline()
    kind, rest = line[:1], line[1:-2]
    if kind == b"-":
        raise SystemExit("redis: " + rest.decode())
    if kind == b"+":
        return rest.decode()
    if kind == b":":
        return int(rest)
    if kind == b"$":
        n = int(rest)
        return None if n < 0 else rd.read(n + 2)[:-2].decode()
    n = int(rest)
    return None if n < 0 else [reply() for _ in range(n)]
def cmd(*args):
    out = b"*%d\r\n" % len(args)
    for a in args:
        a = str(a).encode()
        out += b"$%d\r\n%s\r\n" % (len(a), a)
    sock.sendall(out)
    return reply()
cmd("SELECT", sys.argv[2])
print(json.dumps(cmd(*sys.argv[3:])))
PY
}

# w3_outbox <org>: the org's outbox records (G6), a JSON list of {field: value}.
w3_outbox() {
  w3_redis_json XRANGE "mc:{$1}:outbox" - + |
    jq -c '[(. // [])[] | .[1] as $f | [range(0; ($f | length); 2) as $i | {key: $f[$i], value: $f[$i + 1]}] | from_entries]'
}

# w3_wait_outbox <org> <n>: wait up to 15 s for the outbox to hold n records,
# then 1 s more, so a second settlement of the same request would show.
w3_wait_outbox() {
  local deadline=$(( $(date +%s) + 15 ))
  while [ "$(w3_outbox "$1" | jq length)" -lt "$2" ] && [ "$(date +%s)" -lt "$deadline" ]; do sleep 0.2; done
  sleep 1
}

# w3_sum_keys <pattern>: the sum of the integer values of every key matching
# the pattern (SCAN; a missing or non-integer value counts 0).
w3_sum_keys() {
  python3 -I - "$SCN_REDIS" "$SCN_REDIS_DB" "$1" <<'PY'
import socket, sys
host, port = sys.argv[1].rsplit(":", 1)
sock = socket.create_connection((host, int(port)), timeout=5)
rd = sock.makefile("rb")
def reply():
    line = rd.readline()
    kind, rest = line[:1], line[1:-2]
    if kind == b"-":
        raise SystemExit("redis: " + rest.decode())
    if kind in (b"+", b":"):
        return rest.decode()
    if kind == b"$":
        n = int(rest)
        return None if n < 0 else rd.read(n + 2)[:-2].decode()
    return [reply() for _ in range(int(rest))]
def cmd(*args):
    out = b"*%d\r\n" % len(args)
    for a in args:
        a = str(a).encode()
        out += b"$%d\r\n%s\r\n" % (len(a), a)
    sock.sendall(out)
    return reply()
cmd("SELECT", sys.argv[2])
total, cursor = 0, "0"
while True:
    cursor, keys = cmd("SCAN", cursor, "MATCH", sys.argv[3], "COUNT", 1000)
    for key in keys:
        try:
            total += int(cmd("GET", key) or 0)
        except (ValueError, SystemExit):
            pass
    if cursor == "0":
        break
print(total)
PY
}
