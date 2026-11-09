# shellcheck shell=bash
# Shared setup of the W2 gate checks (gates/W2_*.sh, integrator-owned): the
# W1 setup (lib/w1_common.sh: SCN_REDIS and a positive SCN_REDIS_DB required,
# scratch under $GATE_HOME) under the W2 lane name and scratch dir, plus the
# managed-credit helpers the W2 checks share. Source it from a gate script
# after setting, when needed, SCN_CONFIG_TEMPLATE and SCN_MOCK_FIXTURES:
#
#   source "$(dirname "$0")/lib/w2_common.sh"
export SCN_LANE=${SCN_LANE:-x6-w2-gate}
GATE_HOME=${GATE_HOME:-$HOME/.futureagi-local/gateway-program/lanes/X6-gates}
export SCN_HOME=${SCN_HOME:-$GATE_HOME/scn-w2}
# shellcheck source=w1_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/w1_common.sh"
W2_FIXTURES=$W1_GATES/fixtures/W2

# w2_entry <request id>: the request's log entry in $SCN_DIR/entry.json (30 s).
w2_entry() { w1_entry "$@"; }

# w2_redis <command> [args...]: one Redis command in the gate's DB; prints the
# reply (an integer or a bulk string).
w2_redis() {
  python3 -I - "$SCN_REDIS" "$SCN_REDIS_DB" "$@" <<'PY'
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
        return "" if n < 0 else rd.read(n + 2)[:-2].decode()
    return [reply() for _ in range(int(rest))]
def cmd(*args):
    out = b"*%d\r\n" % len(args)
    for a in args:
        a = str(a).encode()
        out += b"$%d\r\n%s\r\n" % (len(a), a)
    sock.sendall(out)
    return reply()
cmd("SELECT", sys.argv[2])
print(cmd(*sys.argv[3:]))
PY
}

# w2_outbox <org>: how many settlement records the org's outbox holds (G6).
w2_outbox() { w2_redis XLEN "mc:{$1}:outbox"; }

# w2_credits <org> <field>: a field of GET /-/orgs/<org>/credits (G6).
w2_credits() {
  scn_admin GET "/-/orgs/$1/credits" >/dev/null
  jq -r ".$2" <"$SCN_DIR/last.body"
}

# w2_upstream_chats: how many chat calls the mock has received.
w2_upstream_chats() { scn_received 0 /chat/completions | jq '.data | length'; }
