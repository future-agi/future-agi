# shellcheck shell=bash
# Shared setup of the managed-credit gate checks (gates/*.sh): lib/gate_common.sh's
# setup (SCN_REDIS and a positive SCN_REDIS_DB required, scratch under
# $GATE_HOME) under its own run name and scratch dir, plus the managed-credit
# helpers those checks share. Source it from a gate script
# after setting, when needed, SCN_CONFIG_TEMPLATE and SCN_MOCK_FIXTURES:
#
#   source "$(dirname "$0")/lib/managed_gate_common.sh"
export SCN_RUN=${SCN_RUN:-gate-managed}
GATE_HOME=${GATE_HOME:-${TMPDIR:-/tmp}/agentcc-gates}
export SCN_HOME=${SCN_HOME:-$GATE_HOME/scn-managed}
# shellcheck source=gate_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/gate_common.sh"
MANAGED_FIXTURES=$GATES_ROOT/fixtures/sources

# managed_entry <request id>: the request's log entry in $SCN_DIR/entry.json (30 s).
managed_entry() { gate_entry "$@"; }

# managed_redis <command> [args...]: one Redis command in the gate's DB; prints the
# reply (an integer or a bulk string).
managed_redis() {
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

# managed_outbox <org>: how many settlement records the org's outbox holds.
managed_outbox() { managed_redis XLEN "mc:{$1}:outbox"; }

# managed_credit_field <org> <field>: a field of GET /-/orgs/<org>/credits.
managed_credit_field() {
  scn_admin GET "/-/orgs/$1/credits" >/dev/null
  jq -r ".$2" <"$SCN_DIR/last.body"
}

# managed_upstream_chats: how many chat calls the mock has received.
managed_upstream_chats() { scn_received 0 /chat/completions | jq '.data | length'; }
